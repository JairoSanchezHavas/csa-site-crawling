"""Security module: Anti-SSRF enforcement, DNS pre-flight verification,
URL sanitization, and domain canonicalization / same-origin evaluation.
"""

import asyncio
import ipaddress
import re
import socket
from typing import List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse, urlsplit, urlunsplit


class SSRFSecurityError(ValueError):
    """Raised when an inspected URL targets an internal or disallowed network destination."""
    pass


class InvalidURLError(ValueError):
    """Raised when a URL is syntactically invalid or uses an unsupported scheme."""
    pass


# Private, loopback, link-local, and reserved IPv4 / IPv6 subnets to block strictly
BANNED_IP_NETWORKS = [
    # IPv4 loopback & local
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("0.0.0.0/8"),
    # RFC 1918 Private networks
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    # Link-local / Cloud Metadata (169.254.169.254 for GCP & AWS)
    ipaddress.ip_network("169.254.0.0/16"),
    # Carrier-grade NAT
    ipaddress.ip_network("100.64.0.0/10"),
    # Documentation & Test networks
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("192.0.0.0/24"),
    # Multicast & Reserved
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("240.0.0.0/4"),
    ipaddress.ip_network("255.255.255.255/32"),
    # IPv6 Loopback, Link-local, Unique Local, Unspecified
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("::ffff:0:0/96"),  # IPv4-mapped IPv6
]

# Explicit hostnames to block immediately without DNS lookup
BANNED_HOSTNAMES: Set[str] = {
    "localhost",
    "localhost.localdomain",
    "local",
    "broadcasthost",
    "metadata.google.internal",
    "metadata.internal",
}

# Known two-part TLD suffixes for apex domain extraction
MULTI_PART_TLDS = {
    "co.uk", "org.uk", "gov.uk", "me.uk",
    "com.mx", "org.mx", "edu.mx", "gob.mx",
    "com.co", "co.za", "com.br", "com.ar",
    "com.au", "net.au", "org.au", "edu.au",
    "com.es", "nom.es", "org.es",
    "com.fr", "co.jp", "ne.jp", "or.jp",
}


def sanitize_url(raw_url: str) -> str:
    """Sanitize, trim, and ensure URL format."""
    if not raw_url:
        raise InvalidURLError("URL cannot be empty")

    url = raw_url.strip()
    # Normalize common accidental double slashes in protocol
    url = re.sub(r"^(https?:)/+", r"\1//", url, flags=re.IGNORECASE)

    parts = urlsplit(url)
    if parts.scheme.lower() not in {"http", "https"}:
        raise InvalidURLError(f"Unsupported scheme '{parts.scheme}'. Only http and https are permitted.")

    if not parts.netloc:
        raise InvalidURLError("URL is missing hostname / netloc")

    return url


def is_ip_banned(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Check whether an IP address belongs to any banned subnet."""
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    ):
        return True

    for net in BANNED_IP_NETWORKS:
        if ip in net:
            return True

    return False


def _resolve_dns_sync(hostname: str, port: int) -> List[Tuple[int, str]]:
    """Synchronous DNS resolution returning (family, ip_address)."""
    try:
        addr_info = socket.getaddrinfo(hostname, port, family=socket.AF_UNSPEC, proto=socket.IPPROTO_TCP)
        results: List[Tuple[int, str]] = []
        for family, _, _, _, sockaddr in addr_info:
            ip_str = sockaddr[0]
            results.append((family, ip_str))
        return results
    except socket.gaierror as exc:
        raise SSRFSecurityError(f"DNS resolution failed for host '{hostname}': {exc}") from exc


async def validate_url_against_ssrf(url: str) -> str:
    """Validate a URL against SSRF attacks.
    
    1. Sanitizes and validates scheme.
    2. Checks banned hostname blacklist.
    3. Resolves DNS asynchronously.
    4. Ensures all resolved IP addresses are public and not within private/cloud-metadata CIDRs.
    
    Returns the sanitized URL on success, or raises SSRFSecurityError / InvalidURLError.
    """
    clean_url = sanitize_url(url)
    parts = urlsplit(clean_url)
    hostname = parts.hostname or ""

    if not hostname:
        raise InvalidURLError("Could not extract valid hostname from URL")

    hostname_lower = hostname.lower().rstrip(".")

    if hostname_lower in BANNED_HOSTNAMES or hostname_lower.endswith(".localhost") or hostname_lower.endswith(".local"):
        raise SSRFSecurityError(f"Access to '{hostname}' is blocked by anti-SSRF policy (internal host)")

    # Check if hostname itself is an IP literal
    try:
        ip_obj = ipaddress.ip_address(hostname_lower)
        if is_ip_banned(ip_obj):
            raise SSRFSecurityError(f"Access to IP '{hostname}' is blocked by anti-SSRF policy (private/reserved IP)")
        return clean_url
    except ValueError:
        # Not an IP literal, proceed to DNS resolution
        pass

    port = parts.port or (443 if parts.scheme.lower() == "https" else 80)

    # Perform DNS lookup in thread pool to avoid blocking the asyncio event loop
    resolved_entries = await asyncio.to_thread(_resolve_dns_sync, hostname_lower, port)

    if not resolved_entries:
        raise SSRFSecurityError(f"DNS lookup returned no address records for '{hostname}'")

    for _, ip_str in resolved_entries:
        try:
            ip_obj = ipaddress.ip_address(ip_str)
            if is_ip_banned(ip_obj):
                raise SSRFSecurityError(
                    f"Access to '{hostname}' is blocked: resolves to private/reserved IP {ip_str}"
                )
        except ValueError as exc:
            raise SSRFSecurityError(f"Invalid resolved IP '{ip_str}' for host '{hostname}'") from exc

    return clean_url


def extract_apex_domain(hostname: str) -> str:
    """Extract apex/registrable root domain from a hostname.
    
    Example:
        'www.example.com' -> 'example.com'
        'blog.news.example.co.uk' -> 'example.co.uk'
        'example.com' -> 'example.com'
    """
    host = hostname.lower().strip().rstrip(".")
    # Remove port if present
    if ":" in host:
        host = host.split(":")[0]

    parts = host.split(".")
    if len(parts) <= 2:
        return host

    last_two = ".".join(parts[-2:])
    if last_two in MULTI_PART_TLDS and len(parts) >= 3:
        return ".".join(parts[-3:])

    return ".".join(parts[-2:])


def normalize_origin_domain(hostname: str) -> str:
    """Normalize domain by stripping 'www.' prefix and lowercasing.
    
    Ensures 'dominio.com' and 'www.dominio.com' resolve to the exact same normalized domain.
    """
    host = hostname.lower().strip().rstrip(".")
    if ":" in host:
        host = host.split(":")[0]
    if host.startswith("www."):
        return host[4:]
    return host


def is_same_origin_or_allowed_subdomain(
    base_url: str,
    target_url: str,
    allow_subdomains: bool = False,
) -> bool:
    """Verify if target_url belongs to the same origin as base_url.
    
    Rules:
    1. Scheme should match (http or https).
    2. 'dominio.com' and 'www.dominio.com' are treated as identical base origin.
    3. If allow_subdomains is True, any sibling subdomain sharing the same apex domain
       (e.g., 'shop.dominio.com' and 'dominio.com') is permitted.
    """
    try:
        base_parts = urlsplit(base_url)
        target_parts = urlsplit(target_url)

        base_host = base_parts.hostname or ""
        target_host = target_parts.hostname or ""

        if not base_host or not target_host:
            return False

        # Rule 1: Normalization of www.dominio.com <-> dominio.com
        norm_base = normalize_origin_domain(base_host)
        norm_target = normalize_origin_domain(target_host)

        if norm_base == norm_target:
            return True

        # Rule 2: Subdomain evaluation
        if allow_subdomains:
            apex_base = extract_apex_domain(base_host)
            apex_target = extract_apex_domain(target_host)
            return apex_base == apex_target

        return False
    except Exception:
        return False

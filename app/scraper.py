"""Dual-mode scraper engine (Playwright Headless Chromium + httpx fallback)
with real-time network hit interception, in-memory dataLayer extraction,
CMP detection, canonical resolution, and leak-proof lifecycle management.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urldefrag, urljoin, urlsplit

import httpx

from app.schemas import (
    AuditPageRequest,
    AuditPageResponse,
    CookieItem,
    DetectedCmp,
    NetworkHit,
    NetworkHitCategory,
    RenderMode,
    WaitUntilMode,
)
from app.security import (
    InvalidURLError,
    SSRFSecurityError,
    is_same_origin_or_allowed_subdomain,
    validate_url_against_ssrf,
)

logger = logging.getLogger("csa.scraper")

# Try importing Playwright; support graceful fallback if binary is missing during local dev
try:
    from playwright.async_api import (
        Browser,
        BrowserContext,
        Page,
        Playwright,
        Request as PlaywrightRequest,
        Response as PlaywrightResponse,
        async_playwright,
    )
    PLAYWRIGHT_INSTALLED = True
except ImportError:
    PLAYWRIGHT_INSTALLED = False
    Browser = Any  # type: ignore
    Playwright = Any  # type: ignore


# Signatures for categorizing network requests
NETWORK_SIGNATURES: List[Tuple[NetworkHitCategory, re.Pattern[str]]] = [
    (NetworkHitCategory.GA4, re.compile(r"google-analytics\.com/(?:g/)?collect|analytics\.google\.com/g/collect", re.IGNORECASE)),
    (NetworkHitCategory.UA, re.compile(r"google-analytics\.com/collect(?!\?v=2)", re.IGNORECASE)),
    (NetworkHitCategory.GTM, re.compile(r"googletagmanager\.com/(?:gtm|gtag)/js", re.IGNORECASE)),
    (NetworkHitCategory.META, re.compile(r"facebook\.com/tr|connect\.facebook\.net", re.IGNORECASE)),
    (NetworkHitCategory.CMP, re.compile(r"cdn\.cookielaw\.org|consent\.cookiebot\.com|cookiebot\.com/uc\.js|didomi\.io|privacy-center\.org|consent\.trustarc\.com|trustarc\.com|usercentrics\.eu", re.IGNORECASE)),
    (NetworkHitCategory.TIKTOK, re.compile(r"analytics\.tiktok\.com", re.IGNORECASE)),
    (NetworkHitCategory.LINKEDIN, re.compile(r"px\.ads\.linkedin\.com|snap\.licdn\.com", re.IGNORECASE)),
]

# Signatures for detecting CMPs via cookie names
CMP_COOKIE_SIGNATURES: Dict[str, str] = {
    "OptanonConsent": "OneTrust",
    "OptanonAlertBoxClosed": "OneTrust",
    "CookieConsent": "Cookiebot",
    "didomi_token": "Didomi",
    "notice_gdpr_prefs": "TrustArc",
    "notice_preferences": "TrustArc",
    "uc_user_interaction": "Usercentrics",
}

# File extensions to ignore during crawling
NON_HTML_EXTENSIONS = (
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico",
    ".css", ".js", ".json", ".xml", ".txt", ".pdf", ".zip",
    ".tar", ".gz", ".mp4", ".mp3", ".avi", ".mov", ".woff",
    ".woff2", ".ttf", ".eot",
)

REALISTIC_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 CSA-Tech-Auditor/1.0"
)

CHROMIUM_FLAGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--single-process",
    "--disable-extensions",
    "--no-zygote",
]


def classify_url(url: str) -> Optional[NetworkHitCategory]:
    """Classify a network URL into a known tracking / analytics category."""
    for category, pattern in NETWORK_SIGNATURES:
        if pattern.search(url):
            return category
    return None


def extract_canonical(html: str) -> Optional[str]:
    """Extract canonical link from HTML content."""
    match = re.search(r'<link\s+[^>]*rel=["\']canonical["\'][^>]*href=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if not match:
        match = re.search(r'<link\s+[^>]*href=["\']([^"\']+)["\'][^>]*rel=["\']canonical["\']', html, re.IGNORECASE)
    return match.group(1).strip() if match else None


def extract_title(html: str) -> Optional[str]:
    """Extract title from HTML content."""
    match = re.search(r'<title[^>]*>(.*?)</title>', html, re.IGNORECASE | re.DOTALL)
    return match.group(1).strip() if match else None


def extract_internal_links(html: str, current_url: str, allow_subdomains: bool = False) -> List[str]:
    """Extract and resolve valid internal links from HTML content."""
    links: Set[str] = set()
    matches = re.findall(r'<a\s+[^>]*href=["\']([^"\']+)["\']', html, re.IGNORECASE)

    for raw_href in matches:
        raw_href = raw_href.strip()
        if not raw_href or raw_href.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
            continue

        resolved = urljoin(current_url, raw_href)
        clean_resolved, _ = urldefrag(resolved)

        # Check scheme
        parsed = urlsplit(clean_resolved)
        if parsed.scheme.lower() not in {"http", "https"}:
            continue

        # Skip assets
        if any(parsed.path.lower().endswith(ext) for ext in NON_HTML_EXTENSIONS):
            continue

        if is_same_origin_or_allowed_subdomain(current_url, clean_resolved, allow_subdomains):
            links.add(clean_resolved)

    return sorted(list(links))


class ScraperEngine:
    """Manages web page auditing using Playwright (Render mode) or httpx (Fast mode)."""

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._lock = asyncio.Lock()

    async def initialize(self) -> bool:
        """Initialize Playwright and launch shared Chromium browser instance."""
        if not PLAYWRIGHT_INSTALLED:
            logger.warning("Playwright is not installed. Scraper will operate in Fast mode only.")
            return False

        async with self._lock:
            if self._browser is None:
                try:
                    self._playwright = await async_playwright().start()
                    self._browser = await self._playwright.chromium.launch(
                        headless=self.headless,
                        args=CHROMIUM_FLAGS,
                    )
                    logger.info("Playwright Chromium browser launched successfully.")
                    return True
                except Exception as exc:
                    logger.error(f"Failed to launch Playwright browser: {exc}")
                    if self._playwright:
                        await self._playwright.stop()
                        self._playwright = None
                    return False
        return True

    async def shutdown(self) -> None:
        """Close browser instance and stop Playwright runtime cleanly."""
        async with self._lock:
            if self._browser:
                try:
                    await self._browser.close()
                except Exception as exc:
                    logger.warning(f"Error closing browser: {exc}")
                self._browser = None

            if self._playwright:
                try:
                    await self._playwright.stop()
                except Exception as exc:
                    logger.warning(f"Error stopping Playwright: {exc}")
                self._playwright = None
            logger.info("Playwright scraper engine shut down.")

    @property
    def is_browser_ready(self) -> bool:
        return self._browser is not None

    async def audit_page(self, req: AuditPageRequest) -> AuditPageResponse:
        """Audit a single page using the requested mode with anti-SSRF pre-check."""
        # 1. Anti-SSRF Pre-flight validation
        try:
            validated_url = await validate_url_against_ssrf(req.url)
        except (SSRFSecurityError, InvalidURLError) as sec_exc:
            return AuditPageResponse(
                success=False,
                url=req.url,
                final_url=req.url,
                http_status=400,
                render_mode_used=req.render_mode,
                load_time_ms=0.0,
                error=f"Security/SSRF rejection: {sec_exc}",
            )

        # 2. Select execution engine
        if req.render_mode == RenderMode.RENDER and self.is_browser_ready:
            return await self._audit_with_playwright(validated_url, req)
        else:
            if req.render_mode == RenderMode.RENDER and not self.is_browser_ready:
                logger.info("Playwright browser not ready; falling back to Fast mode.")
            return await self._audit_with_httpx(validated_url, req)

    async def _audit_with_playwright(self, url: str, req: AuditPageRequest) -> AuditPageResponse:
        """Audit page using Headless Chromium with full JavaScript rendering and network tracing."""
        assert self._browser is not None

        start_time = time.perf_counter()
        network_hits: List[NetworkHit] = []
        detected_cmps_dict: Dict[str, DetectedCmp] = {}

        # Safe Playwright context creation
        context: Optional[BrowserContext] = None
        page: Optional[Page] = None

        timeout_sec = req.timeout_ms / 1000.0

        try:
            context = await self._browser.new_context(
                user_agent=REALISTIC_USER_AGENT,
                viewport={"width": 1440, "height": 900},
                ignore_https_errors=True,
                java_script_enabled=True,
            )

            page = await context.new_page()
            page.set_default_timeout(req.timeout_ms)
            page.set_default_navigation_timeout(req.timeout_ms)

            # Request interception handler
            def on_request(request: PlaywrightRequest) -> None:
                req_url = request.url
                cat = classify_url(req_url)
                if cat:
                    network_hits.append(
                        NetworkHit(
                            category=cat,
                            url=req_url,
                            method=request.method,
                            resource_type=request.resource_type,
                            timestamp_ms=round((time.perf_counter() - start_time) * 1000, 2),
                        )
                    )
                    # Check for CMP network hits
                    if cat == NetworkHitCategory.CMP:
                        cmp_name = "Generic CMP"
                        if "cookielaw.org" in req_url or "onetrust" in req_url:
                            cmp_name = "OneTrust"
                        elif "cookiebot" in req_url:
                            cmp_name = "Cookiebot"
                        elif "didomi" in req_url:
                            cmp_name = "Didomi"
                        elif "trustarc" in req_url:
                            cmp_name = "TrustArc"
                        elif "usercentrics" in req_url:
                            cmp_name = "Usercentrics"
                        detected_cmps_dict[cmp_name] = DetectedCmp(
                            name=cmp_name,
                            detected_by="network",
                            details=f"Hit: {req_url[:120]}",
                        )

            def on_response(response: PlaywrightResponse) -> None:
                # Update status of existing hit if matching
                resp_url = response.url
                for hit in reversed(network_hits):
                    if hit.url == resp_url and hit.status is None:
                        hit.status = response.status
                        break

            page.on("request", on_request)
            page.on("response", on_response)

            # Navigate to target page
            wait_condition = req.wait_until.value
            playwright_response = await page.goto(url, wait_until=wait_condition, timeout=req.timeout_ms)

            # Additional prudent stabilization for dynamic single page applications
            await asyncio.sleep(1.0)

            final_url = page.url
            http_status = playwright_response.status if playwright_response else 200

            # Validate final redirect destination against Anti-SSRF
            try:
                await validate_url_against_ssrf(final_url)
            except SSRFSecurityError as ssrf_err:
                return AuditPageResponse(
                    success=False,
                    url=url,
                    final_url=final_url,
                    http_status=400,
                    render_mode_used=RenderMode.RENDER,
                    load_time_ms=round((time.perf_counter() - start_time) * 1000, 2),
                    error=f"Redirect targeted an illegal/internal IP: {ssrf_err}",
                )

            # Evaluate real in-memory dataLayer directly from browser JavaScript environment
            data_layer = await page.evaluate(
                """() => {
                    try {
                        if (Array.isArray(window.dataLayer)) {
                            return JSON.parse(JSON.stringify(window.dataLayer));
                        } else if (window.dataLayer && typeof window.dataLayer === 'object') {
                            return [JSON.parse(JSON.stringify(window.dataLayer))];
                        }
                        return [];
                    } catch (e) {
                        return [];
                    }
                }"""
            )

            # Check in-memory global variables for CMPs
            cmp_globals = await page.evaluate(
                """() => {
                    const result = {};
                    if (window.OneTrust || window.OnetrustActiveGroups) result['OneTrust'] = true;
                    if (window.Cookiebot) result['Cookiebot'] = true;
                    if (window.Didomi) result['Didomi'] = true;
                    if (window.truste) result['TrustArc'] = true;
                    if (window.UC_UI) result['Usercentrics'] = true;
                    return result;
                }"""
            )
            for cmp_name in cmp_globals:
                detected_cmps_dict[cmp_name] = DetectedCmp(
                    name=cmp_name,
                    detected_by="global_var",
                    details=f"window.{cmp_name} present in browser memory",
                )

            # Retrieve browser cookies
            raw_cookies = await context.cookies()
            cookie_items: List[CookieItem] = []
            for c in raw_cookies:
                c_name = c.get("name", "")
                cookie_items.append(
                    CookieItem(
                        name=c_name,
                        domain=c.get("domain", ""),
                        path=c.get("path", "/"),
                        secure=c.get("secure", False),
                        http_only=c.get("httpOnly", False),
                        same_site=c.get("sameSite"),
                    )
                )
                if c_name in CMP_COOKIE_SIGNATURES:
                    cmp_name = CMP_COOKIE_SIGNATURES[c_name]
                    detected_cmps_dict[cmp_name] = DetectedCmp(
                        name=cmp_name,
                        detected_by="cookie",
                        details=f"Cookie: {c_name}",
                    )

            # Rendered HTML content
            html_content = await page.content()
            title = await page.title()
            canonical_url = extract_canonical(html_content)
            internal_links = extract_internal_links(html_content, final_url, req.allow_subdomains)
            is_same_origin = is_same_origin_or_allowed_subdomain(url, final_url, req.allow_subdomains)

            # Response headers
            resp_headers = playwright_response.headers if playwright_response else {}
            sec_headers = {
                k: v for k, v in resp_headers.items()
                if k.lower() in {
                    "strict-transport-security",
                    "content-security-policy",
                    "x-frame-options",
                    "x-content-type-options",
                    "referrer-policy",
                    "permissions-policy",
                }
            }

            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)

            return AuditPageResponse(
                success=True,
                url=url,
                final_url=final_url,
                http_status=http_status,
                title=title,
                canonical_url=canonical_url,
                is_same_origin=is_same_origin,
                render_mode_used=RenderMode.RENDER,
                load_time_ms=elapsed_ms,
                detected_cmps=list(detected_cmps_dict.values()),
                data_layer=data_layer if isinstance(data_layer, list) else [],
                network_hits=network_hits,
                internal_links=internal_links,
                cookies=cookie_items,
                security_headers=sec_headers,
                html_preview=html_content[:3000] if html_content else None,
                html=html_content if req.include_html else None,
            )

        except Exception as exc:
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            logger.warning(f"Playwright navigation failed for {url}: {exc}")
            return AuditPageResponse(
                success=False,
                url=url,
                final_url=url,
                http_status=500,
                render_mode_used=RenderMode.RENDER,
                load_time_ms=elapsed_ms,
                error=f"Playwright error: {str(exc)}",
            )
        finally:
            # Rigorous resource cleanup to prevent memory leaks in Cloud Run
            if page:
                try:
                    await page.close()
                except Exception:
                    pass
            if context:
                try:
                    await context.close()
                except Exception:
                    pass

    async def _audit_with_httpx(self, url: str, req: AuditPageRequest) -> AuditPageResponse:
        """Fast HTTP audit using httpx with redirect validation and static pattern extraction."""
        start_time = time.perf_counter()
        timeout_sec = req.timeout_ms / 1000.0

        try:
            async with httpx.AsyncClient(
                headers={"User-Agent": REALISTIC_USER_AGENT},
                follow_redirects=True,
                timeout=timeout_sec,
                verify=False,
            ) as client:
                response = await client.get(url)

                final_url = str(response.url)
                # Anti-SSRF check on redirect chain
                await validate_url_against_ssrf(final_url)

                html_content = response.text
                http_status = response.status_code
                title = extract_title(html_content)
                canonical_url = extract_canonical(html_content)
                internal_links = extract_internal_links(html_content, final_url, req.allow_subdomains)
                is_same_origin = is_same_origin_or_allowed_subdomain(url, final_url, req.allow_subdomains)

                # Static network hit detection via <script> and <img> tags
                network_hits: List[NetworkHit] = []
                detected_cmps_dict: Dict[str, DetectedCmp] = {}

                script_urls = re.findall(r'<script\s+[^>]*src=["\']([^"\']+)["\']', html_content, re.IGNORECASE)
                for s_url in script_urls:
                    resolved_s = urljoin(final_url, s_url)
                    cat = classify_url(resolved_s)
                    if cat:
                        network_hits.append(
                            NetworkHit(
                                category=cat,
                                url=resolved_s,
                                method="GET",
                                resource_type="script",
                            )
                        )
                        if cat == NetworkHitCategory.CMP:
                            detected_cmps_dict["Detected CMP"] = DetectedCmp(
                                name="CMP Script",
                                detected_by="script",
                                details=resolved_s[:120],
                            )

                # Extract static dataLayer pushes
                data_layer: List[Any] = []
                dl_matches = re.findall(r'dataLayer\.push\((.*?)\);', html_content, re.DOTALL)
                for raw_dl in dl_matches[:10]:
                    data_layer.append({"static_push_raw": raw_dl.strip()})

                # Cookies from response
                cookie_items: List[CookieItem] = []
                for name, value in response.cookies.items():
                    cookie_items.append(CookieItem(name=name, domain=response.url.host))
                    if name in CMP_COOKIE_SIGNATURES:
                        cmp_name = CMP_COOKIE_SIGNATURES[name]
                        detected_cmps_dict[cmp_name] = DetectedCmp(
                            name=cmp_name,
                            detected_by="cookie",
                            details=f"Cookie: {name}",
                        )

                # Security headers
                sec_headers = {
                    k: v for k, v in response.headers.items()
                    if k.lower() in {
                        "strict-transport-security",
                        "content-security-policy",
                        "x-frame-options",
                        "x-content-type-options",
                        "referrer-policy",
                        "permissions-policy",
                    }
                }

                elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)

                return AuditPageResponse(
                    success=True,
                    url=url,
                    final_url=final_url,
                    http_status=http_status,
                    title=title,
                    canonical_url=canonical_url,
                    is_same_origin=is_same_origin,
                    render_mode_used=RenderMode.FAST,
                    load_time_ms=elapsed_ms,
                    detected_cmps=list(detected_cmps_dict.values()),
                    data_layer=data_layer,
                    network_hits=network_hits,
                    internal_links=internal_links,
                    cookies=cookie_items,
                    security_headers=sec_headers,
                    html_preview=html_content[:3000] if html_content else None,
                    html=html_content if req.include_html else None,
                )

        except (SSRFSecurityError, InvalidURLError) as sec_err:
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            return AuditPageResponse(
                success=False,
                url=url,
                final_url=url,
                http_status=400,
                render_mode_used=RenderMode.FAST,
                load_time_ms=elapsed_ms,
                error=f"SSRF security error: {sec_err}",
            )
        except Exception as exc:
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            logger.warning(f"httpx fetch failed for {url}: {exc}")
            return AuditPageResponse(
                success=False,
                url=url,
                final_url=url,
                http_status=500,
                render_mode_used=RenderMode.FAST,
                load_time_ms=elapsed_ms,
                error=f"HTTP request error: {str(exc)}",
            )

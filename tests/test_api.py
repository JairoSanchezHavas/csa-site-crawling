"""Test suite for CSA Tech Auditor backend:
- /health endpoint status
- Anti-SSRF private IP and metadata blocking
- Request payload validation
- Domain canonicalization and same-origin / subdomain logic
- Audit response structure
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas import RenderMode
from app.security import (
    SSRFSecurityError,
    extract_apex_domain,
    is_same_origin_or_allowed_subdomain,
    normalize_origin_domain,
    validate_url_against_ssrf,
)

client = TestClient(app)


def test_health_check_endpoint():
    """Verify GET /health returns 200 and valid health payload."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "CSA Tech Auditor" in data["service"]
    assert "version" in data
    assert "playwright_ready" in data


@pytest.mark.parametrize(
    "forbidden_url",
    [
        "http://localhost",
        "http://localhost:8080",
        "http://127.0.0.1",
        "http://127.0.0.1:8000/secret",
        "http://10.0.0.1",
        "http://172.16.0.1",
        "http://192.168.1.1",
        "http://169.254.169.254/computeMetadata/v1/",
        "http://169.254.1.1",
        "http://0.0.0.0",
        "http://broadcasthost",
    ],
)
def test_anti_ssrf_blocks_private_targets(forbidden_url):
    """Ensure POST /api/audit-page rejects loopback, private, and cloud metadata targets."""
    response = client.post(
        "/api/audit-page",
        json={"url": forbidden_url, "render_mode": "fast"},
    )
    assert response.status_code == 400
    detail = response.json().get("detail", "")
    assert "Security/SSRF validation rejected" in detail or "blocked" in detail


@pytest.mark.asyncio
async def test_anti_ssrf_direct_validator():
    """Directly test validate_url_against_ssrf function raises SSRFSecurityError."""
    with pytest.raises(SSRFSecurityError):
        await validate_url_against_ssrf("http://127.0.0.1/admin")

    with pytest.raises(SSRFSecurityError):
        await validate_url_against_ssrf("http://169.254.169.254/latest/meta-data/")

    with pytest.raises(SSRFSecurityError):
        await validate_url_against_ssrf("http://localhost:3000")


def test_payload_validation_invalid_scheme():
    """Ensure invalid schemes like ftp:// or file:// are rejected by Pydantic."""
    response = client.post(
        "/api/audit-page",
        json={"url": "ftp://example.com/file.txt"},
    )
    assert response.status_code == 422


def test_payload_validation_missing_url():
    """Ensure missing url field returns 422 Unprocessable Entity."""
    response = client.post("/api/audit-page", json={})
    assert response.status_code == 422


def test_same_origin_and_canonical_domain_logic():
    """Verify that domain.com and www.domain.com are treated as the same origin,
    and subdomains are handled strictly according to allow_subdomains.
    """
    # 1. www vs non-www
    assert is_same_origin_or_allowed_subdomain(
        "https://example.com", "https://www.example.com", allow_subdomains=False
    ) is True

    assert is_same_origin_or_allowed_subdomain(
        "https://www.example.com", "https://example.com", allow_subdomains=False
    ) is True

    # 2. Paths on same origin
    assert is_same_origin_or_allowed_subdomain(
        "https://example.com/about", "https://www.example.com/contact", allow_subdomains=False
    ) is True

    # 3. Different subdomains with allow_subdomains=False
    assert is_same_origin_or_allowed_subdomain(
        "https://example.com", "https://blog.example.com", allow_subdomains=False
    ) is False

    # 4. Different subdomains with allow_subdomains=True
    assert is_same_origin_or_allowed_subdomain(
        "https://example.com", "https://blog.example.com", allow_subdomains=True
    ) is True

    assert is_same_origin_or_allowed_subdomain(
        "https://shop.example.com", "https://checkout.example.com", allow_subdomains=True
    ) is True

    # 5. Completely different domains
    assert is_same_origin_or_allowed_subdomain(
        "https://example.com", "https://attacker.com", allow_subdomains=True
    ) is False


def test_extract_apex_domain():
    """Verify apex domain extraction handles simple and multi-part TLDs."""
    assert extract_apex_domain("www.example.com") == "example.com"
    assert extract_apex_domain("blog.example.co.uk") == "example.co.uk"
    assert extract_apex_domain("tienda.marca.com.mx") == "marca.com.mx"
    assert normalize_origin_domain("www.havas.com") == "havas.com"


def test_audit_page_mocked_success(monkeypatch):
    """Test /api/audit-page endpoint returns structured audit response when scraper succeeds."""
    from app.main import scraper_engine
    from app.schemas import AuditPageResponse, DetectedCmp, NetworkHit, NetworkHitCategory

    async def fake_audit_page(req):
        return AuditPageResponse(
            success=True,
            url=req.url,
            final_url="https://www.example.com",
            http_status=200,
            title="Example Domain",
            canonical_url="https://www.example.com",
            is_same_origin=True,
            render_mode_used=req.render_mode,
            load_time_ms=120.5,
            detected_cmps=[
                DetectedCmp(name="OneTrust", detected_by="cookie", details="Cookie: OptanonConsent")
            ],
            data_layer=[{"event": "page_view", "page_title": "Example"}],
            network_hits=[
                NetworkHit(
                    category=NetworkHitCategory.GA4,
                    url="https://www.google-analytics.com/g/collect?v=2&tid=G-XXXXX",
                    method="GET",
                    status=204,
                ),
                NetworkHit(
                    category=NetworkHitCategory.GTM,
                    url="https://www.googletagmanager.com/gtm.js?id=GTM-XXXX",
                    method="GET",
                    status=200,
                ),
            ],
            internal_links=["https://www.example.com/about", "https://www.example.com/contact"],
            security_headers={"strict-transport-security": "max-age=31536000"},
        )

    monkeypatch.setattr(scraper_engine, "audit_page", fake_audit_page)

    # Note: example.com resolves to public IP 93.184.215.14
    response = client.post(
        "/api/audit-page",
        json={"url": "https://example.com", "render_mode": "fast"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["final_url"] == "https://www.example.com"
    assert len(data["detected_cmps"]) == 1
    assert data["detected_cmps"][0]["name"] == "OneTrust"
    assert len(data["data_layer"]) == 1
    assert data["data_layer"][0]["event"] == "page_view"
    assert len(data["network_hits"]) == 2
    assert data["network_hits"][0]["category"] == "ga4"


def test_crawl_site_mocked(monkeypatch):
    """Test /api/crawl-site returns aggregated multi-page crawl report."""
    from app.main import scraper_engine
    from app.schemas import AuditPageResponse

    async def fake_audit_page(req):
        return AuditPageResponse(
            success=True,
            url=req.url,
            final_url=req.url,
            http_status=200,
            title="Page " + req.url,
            is_same_origin=True,
            render_mode_used=req.render_mode,
            load_time_ms=50.0,
            internal_links=["https://example.com/p2"] if req.url == "https://example.com" else [],
        )

    monkeypatch.setattr(scraper_engine, "audit_page", fake_audit_page)

    response = client.post(
        "/api/crawl-site",
        json={"start_url": "https://example.com", "max_pages": 2},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["start_url"] == "https://example.com"
    assert data["total_pages_audited"] == 2
    assert len(data["results"]) == 2


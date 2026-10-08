"""Pydantic v2 schemas for request and response models in CSA Tech Auditor.
"""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, HttpUrl, field_validator


class RenderMode(str, Enum):
    """Execution engine for page inspection."""
    RENDER = "render"  # Headless Chromium via Playwright (JS execution + network tracing)
    FAST = "fast"      # Asynchronous HTTP request via httpx (fallback / static only)


class WaitUntilMode(str, Enum):
    """Playwright page navigation stabilization strategy."""
    NETWORKIDLE = "networkidle"
    DOMCONTENTLOADED = "domcontentloaded"
    LOAD = "load"


class NetworkHitCategory(str, Enum):
    """Classification of intercepted analytics and tracking network hits."""
    GA4 = "ga4"
    UA = "ua"
    GTM = "gtm"
    META = "meta"
    CMP = "cmp"
    TIKTOK = "tiktok"
    LINKEDIN = "linkedin"
    OTHER_ANALYTICS = "other_analytics"


class NetworkHit(BaseModel):
    """Information about an intercepted network request."""
    category: NetworkHitCategory
    url: str
    method: str = "GET"
    status: Optional[int] = None
    resource_type: Optional[str] = None
    timestamp_ms: Optional[float] = None


class DetectedCmp(BaseModel):
    """Information regarding a detected Consent Management Platform."""
    name: str
    detected_by: str = Field(description="Detection vector: cookie, script, network, or global_var")
    details: Optional[str] = None


class CookieItem(BaseModel):
    """Structured cookie metadata extracted from page session."""
    name: str
    domain: str
    path: str = "/"
    secure: bool = False
    http_only: bool = False
    same_site: Optional[str] = None


class AuditPageRequest(BaseModel):
    """Request payload for auditing an individual webpage."""
    url: str = Field(..., description="Target webpage URL to audit")
    render_mode: RenderMode = Field(default=RenderMode.RENDER, description="Fetch engine: render or fast")
    timeout_ms: int = Field(default=20000, ge=3000, le=60000, description="Page timeout in milliseconds")
    allow_subdomains: bool = Field(default=False, description="Whether sibling subdomains are considered same origin")
    wait_until: WaitUntilMode = Field(default=WaitUntilMode.NETWORKIDLE, description="Playwright wait condition")
    include_html: bool = Field(default=False, description="Include full rendered HTML string in response")

    @field_validator("url")
    @classmethod
    def validate_url_scheme(cls, v: str) -> str:
        clean = v.strip()
        if not (clean.startswith("http://") or clean.startswith("https://")):
            raise ValueError("URL must begin with http:// or https://")
        return clean


class AuditPageResponse(BaseModel):
    """Audit results for a single webpage."""
    success: bool
    url: str
    final_url: str
    http_status: int
    title: Optional[str] = None
    canonical_url: Optional[str] = None
    is_same_origin: bool = True
    render_mode_used: RenderMode
    load_time_ms: float
    detected_cmps: List[DetectedCmp] = Field(default_factory=list)
    data_layer: List[Any] = Field(default_factory=list)
    network_hits: List[NetworkHit] = Field(default_factory=list)
    internal_links: List[str] = Field(default_factory=list)
    cookies: List[CookieItem] = Field(default_factory=list)
    security_headers: Dict[str, str] = Field(default_factory=dict)
    html_preview: Optional[str] = None
    html: Optional[str] = None
    error: Optional[str] = None


class CrawlSiteRequest(BaseModel):
    """Request payload for server-side site crawling."""
    start_url: str = Field(..., description="Starting homepage URL for crawl")
    max_pages: int = Field(default=15, ge=1, le=25, description="Maximum internal pages to crawl (default 15)")
    render_mode: RenderMode = Field(default=RenderMode.RENDER, description="Fetch engine: render or fast")
    allow_subdomains: bool = Field(default=False, description="Include sibling subdomains in crawl scope")
    timeout_per_page_ms: int = Field(default=20000, ge=3000, le=45000, description="Timeout per page in ms")

    @field_validator("start_url")
    @classmethod
    def validate_start_url(cls, v: str) -> str:
        clean = v.strip()
        if not (clean.startswith("http://") or clean.startswith("https://")):
            raise ValueError("start_url must begin with http:// or https://")
        return clean


class CrawlSiteResponse(BaseModel):
    """Aggregate report from a multi-page site crawl."""
    start_url: str
    final_base_url: str
    total_pages_audited: int
    results: List[AuditPageResponse]
    duration_ms: float


class HealthResponse(BaseModel):
    """Healthcheck endpoint payload."""
    status: str = "ok"
    service: str = "csa-site-crawling"
    version: str = "1.0.0"
    playwright_ready: bool = True

"""FastAPI Application entrypoint for CSA Tech Auditor · Havas.
Provides atomic /api/audit-page, batch /api/crawl-site, and /health endpoints.
"""

from contextlib import asynccontextmanager
import logging
import os
import time
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.schemas import (
    AuditPageRequest,
    AuditPageResponse,
    CrawlSiteRequest,
    CrawlSiteResponse,
    HealthResponse,
    RenderMode,
    WaitUntilMode,
)
from app.scraper import ScraperEngine
from app.security import (
    InvalidURLError,
    SSRFSecurityError,
    is_same_origin_or_allowed_subdomain,
    validate_url_against_ssrf,
)

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("csa.main")

# Global singleton scraper engine
scraper_engine = ScraperEngine(headless=settings.PLAYWRIGHT_HEADLESS)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan context manager for initializing and tearing down browser engine."""
    logger.info("Initializing CSA Tech Auditor backend...")
    ready = await scraper_engine.initialize()
    if ready:
        logger.info("Headless Playwright Chromium browser initialized.")
    else:
        logger.warning("Running with httpx Fast mode fallback.")

    yield

    logger.info("Tearing down backend and freeing browser resources...")
    await scraper_engine.shutdown()
    logger.info("Shutdown complete.")


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Backend for digital analytics, GTM/GA4/CMP auditing, and SPA crawling.",
    lifespan=lifespan,
)

# Configure CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse, tags=["Monitoring"])
async def health_check() -> HealthResponse:
    """Cloud Run health check endpoint."""
    return HealthResponse(
        status="ok",
        service=settings.PROJECT_NAME,
        version=settings.VERSION,
        playwright_ready=scraper_engine.is_browser_ready,
    )


@app.post("/api/audit-page", response_model=AuditPageResponse, tags=["Auditing"])
async def audit_page_endpoint(request_body: AuditPageRequest) -> AuditPageResponse:
    """Audit a single webpage in detail.
    
    Extracts rendered HTML, cookies, window.dataLayer, CMP signals,
    and intercepts analytics network hits (GA4, GTM, Meta, TikTok, LinkedIn).
    """
    try:
        # Pre-validate URL with SSRF checker
        await validate_url_against_ssrf(request_body.url)
    except (SSRFSecurityError, InvalidURLError) as exc:
        logger.warning(f"Rejected audit request for '{request_body.url}': {exc}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Security/SSRF validation rejected target URL: {exc}",
        )

    # Perform page audit
    response = await scraper_engine.audit_page(request_body)
    return response


@app.get("/api/audit-page", response_model=AuditPageResponse, tags=["Auditing"])
async def audit_page_get_endpoint(
    url: str = Query(..., description="Target webpage URL to audit"),
    render_mode: RenderMode = Query(default=RenderMode.RENDER, description="Fetch engine: render or fast"),
    timeout_ms: int = Query(default=20000, ge=3000, le=60000, description="Page timeout in ms"),
    allow_subdomains: bool = Query(default=False, description="Whether sibling subdomains are allowed"),
    wait_until: WaitUntilMode = Query(default=WaitUntilMode.NETWORKIDLE, description="Playwright wait condition"),
    include_html: bool = Query(default=True, description="Include full rendered HTML string in response"),
) -> AuditPageResponse:
    """Audit a single webpage via GET query parameter with preloaded defaults:
    
    - render_mode: render
    - timeout_ms: 20000
    - allow_subdomains: false
    - wait_until: networkidle
    - include_html: true
    """
    clean_url = url.strip()
    if not (clean_url.startswith("http://") or clean_url.startswith("https://")):
        clean_url = "https://" + clean_url

    req = AuditPageRequest(
        url=clean_url,
        render_mode=render_mode,
        timeout_ms=timeout_ms,
        allow_subdomains=allow_subdomains,
        wait_until=wait_until,
        include_html=include_html,
    )

    try:
        await validate_url_against_ssrf(req.url)
    except (SSRFSecurityError, InvalidURLError) as exc:
        logger.warning(f"Rejected audit GET request for '{req.url}': {exc}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Security/SSRF validation rejected target URL: {exc}",
        )

    return await scraper_engine.audit_page(req)


@app.post("/api/crawl-site", response_model=CrawlSiteResponse, tags=["Auditing"])
async def crawl_site_endpoint(request_body: CrawlSiteRequest) -> CrawlSiteResponse:
    """Server-side crawler for up to 15 internal pages of a site.
    
    Discovers and follows internal links conforming to same-origin / allowed-subdomain rules.
    """
    start_time = time.perf_counter()

    try:
        validated_start_url = await validate_url_against_ssrf(request_body.start_url)
    except (SSRFSecurityError, InvalidURLError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Security/SSRF validation rejected starting URL: {exc}",
        )

    visited_urls: set[str] = set()
    queue: list[str] = [validated_start_url]
    audit_results: list[AuditPageResponse] = []
    final_base_url = validated_start_url

    max_pages = min(request_body.max_pages, settings.MAX_CRAWL_PAGES)

    while queue and len(visited_urls) < max_pages:
        current_url = queue.pop(0)
        if current_url in visited_urls:
            continue

        visited_urls.add(current_url)
        logger.info(f"Crawling page {len(visited_urls)}/{max_pages}: {current_url}")

        audit_req = AuditPageRequest(
            url=current_url,
            render_mode=request_body.render_mode,
            timeout_ms=request_body.timeout_per_page_ms,
            allow_subdomains=request_body.allow_subdomains,
            include_html=False,
        )

        page_result = await scraper_engine.audit_page(audit_req)
        audit_results.append(page_result)

        # On the first page, update final base URL to reflect canonical/redirect origin
        if len(visited_urls) == 1 and page_result.success:
            final_base_url = page_result.final_url

        # Enqueue new internal links discovered on this page
        if page_result.success and page_result.internal_links:
            for link in page_result.internal_links:
                if link not in visited_urls and link not in queue:
                    # Enforce origin check against final base URL
                    if is_same_origin_or_allowed_subdomain(
                        final_base_url, link, request_body.allow_subdomains
                    ):
                        queue.append(link)

    total_duration_ms = round((time.perf_counter() - start_time) * 1000, 2)

    return CrawlSiteResponse(
        start_url=request_body.start_url,
        final_base_url=final_base_url,
        total_pages_audited=len(audit_results),
        results=audit_results,
        duration_ms=total_duration_ms,
    )


# Serve Static SPA
static_path = os.path.join(os.getcwd(), settings.STATIC_DIR)
if os.path.isdir(static_path):
    app.mount("/static", StaticFiles(directory=static_path), name="static")


@app.get("/", include_in_schema=False)
async def serve_index() -> FileResponse:
    """Serve the single-page application index.html."""
    index_file = os.path.join(static_path, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file, media_type="text/html")
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"message": "Frontend index.html not found in static directory"},
    )

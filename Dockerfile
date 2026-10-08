# ==============================================================================
# Dockerfile: CSA Tech Auditor · Havas
# Target: Google Cloud Run (Containerized FastAPI + Playwright Headless Chromium)
# ==============================================================================

FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

# Set environment variables for production execution
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    HOME=/home/pwuser

# Set working directory
WORKDIR /app

# Upgrade pip and install production dependencies
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/requirements.txt

# Ensure Chromium browser binary is present and dependencies configured
RUN playwright install chromium

# Copy application source code and static assets
COPY app /app/app
COPY static /app/static

# Set permissions for the pre-existing non-root user (pwuser)
RUN chown -R pwuser:pwuser /app /ms-playwright

# Switch to non-root user for security compliance
USER pwuser

# Document exposed container port
EXPOSE 8080

# Run uvicorn server binding to dynamic Cloud Run $PORT (defaults to 8080)
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]

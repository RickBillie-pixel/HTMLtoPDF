# =============================================================================
# COMPLETE DOCKERFILE - HTML TO PDF & PDF TO WORD CONVERTER
# =============================================================================
# Deze Dockerfile bevat ALLE dependencies voor:
# - HTML naar PDF conversie (Playwright + Chromium)
# - PDF naar Word conversie (pdf2docx)
# - YER header afbeeldingen
# - Alle fonts en system libraries
# =============================================================================

# Start met Microsoft's officiële Playwright Python image
# Deze image bevat al:
# - Python 3.11
# - Chromium browser (pre-installed!)
# - Alle Playwright dependencies
# - Alle system libraries voor browser rendering
# Dit lost het netwerk download probleem op!
FROM mcr.microsoft.com/playwright/python:v1.41.0-jammy

# Werk directory
WORKDIR /app

# =============================================================================
# EXTRA SYSTEM DEPENDENCIES
# Voor pdf2docx (PDF naar Word conversie) en algemene functionaliteit
# =============================================================================
RUN apt-get update && apt-get install -y \
    # Build tools voor Python packages met C extensions
    python3-dev \
    build-essential \
    gcc \
    g++ \
    make \
    # Voor pdf2docx library (Python-docx dependencies)
    libxml2-dev \
    libxslt1-dev \
    zlib1g-dev \
    # Extra font support (bovenop wat Playwright image al heeft)
    fonts-liberation \
    fonts-dejavu-core \
    fonts-freefont-ttf \
    fonts-noto-color-emoji \
    fonts-noto-cjk \
    fonts-unifont \
    # Image processing libraries
    libjpeg-dev \
    libpng-dev \
    libtiff-dev \
    # SSL/TLS voor HTTPS downloads
    ca-certificates \
    openssl \
    # Cleanup om image klein te houden
    && rm -rf /var/lib/apt/lists/*

# =============================================================================
# PYTHON DEPENDENCIES
# =============================================================================
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Verify Playwright is correct geïnstalleerd
# (Chromium is al in de base image, geen download nodig!)
RUN python -c "from playwright.sync_api import sync_playwright; print('Playwright OK')"

# =============================================================================
# APPLICATIE CODE
# =============================================================================
COPY main.py .

# =============================================================================
# OUTPUT DIRECTORIES
# Voor gegenereerde PDF en Word bestanden
# =============================================================================
RUN mkdir -p /app/static/output && \
    mkdir -p /app/static/word_output && \
    # Zorg dat directories schrijfbaar zijn
    chmod -R 777 /app/static

# =============================================================================
# NETWORK & RUNTIME CONFIGURATIE
# =============================================================================
# Port voor FastAPI
EXPOSE 8000

# Environment variables
ENV PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# =============================================================================
# HEALTH CHECK
# Controleer of de API responding is
# =============================================================================
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# =============================================================================
# START COMMAND
# Uvicorn server met 1 worker (geschikt voor Render free tier)
# =============================================================================
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

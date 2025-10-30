# =============================================================================
# COMPLETE DOCKERFILE - HTML TO PDF & PDF TO WORD CONVERTER
# =============================================================================
FROM mcr.microsoft.com/playwright/python:v1.41.0-jammy

WORKDIR /app

# =============================================================================
# EXTRA SYSTEM DEPENDENCIES
# Voor pdf2docx (PDF naar Word conversie) en algemene functionaliteit
# =============================================================================
RUN apt-get update && apt-get install -y \
    python3-dev \
    build-essential \
    gcc \
    g++ \
    make \
    libxml2-dev \
    libxslt1-dev \
    zlib1g-dev \
    fonts-liberation \
    fonts-dejavu-core \
    fonts-freefont-ttf \
    fonts-noto-color-emoji \
    fonts-noto-cjk \
    fonts-unifont \
    libjpeg-dev \
    libpng-dev \
    libtiff-dev \
    ca-certificates \
    openssl && \
    \
    # ✅ Voeg Microsoft Verdana en andere TrueType fonts toe
    echo "ttf-mscorefonts-installer msttcorefonts/accepted-mscorefonts-eula select true" | debconf-set-selections && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y ttf-mscorefonts-installer fontconfig && \
    fc-cache -f -v && \
    \
    # Cleanup
    rm -rf /var/lib/apt/lists/*

# =============================================================================
# PYTHON DEPENDENCIES
# =============================================================================
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Verify Playwright is correct geïnstalleerd
RUN python -c "from playwright.sync_api import sync_playwright; print('Playwright OK')"

# =============================================================================
# APPLICATIE CODE
# =============================================================================
COPY main.py .

# =============================================================================
# OUTPUT DIRECTORIES
# =============================================================================
RUN mkdir -p /app/static/output && \
    mkdir -p /app/static/word_output && \
    chmod -R 777 /app/static

# =============================================================================
# NETWORK & RUNTIME CONFIGURATIE
# =============================================================================
EXPOSE 8000

ENV PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# =============================================================================
# HEALTH CHECK
# =============================================================================
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# =============================================================================
# START COMMAND
# =============================================================================
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

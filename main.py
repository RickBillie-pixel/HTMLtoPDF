from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from playwright.async_api import async_playwright
import os
import base64
from pathlib import Path
import logging

# Logging configuratie
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="HTML to PDF Converter",
    description="Convert HTML to PDF with full CSS support using Chromium",
    version="1.0.0"
)

# CORS configuratie voor n8n
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Output directory aanmaken
OUTPUT_DIR = Path("/app/static/output")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Statische files hosten
app.mount("/output", StaticFiles(directory=str(OUTPUT_DIR)), name="output")


class ConversionRequest(BaseModel):
    html: str = Field(..., description="Volledige HTML string om te converteren")
    filename: str = Field(..., description="Naam van het PDF bestand (bijv. cv_1234.pdf)")
    return_base64: bool = Field(False, description="Optioneel: return PDF als base64 string")
    
    class Config:
        json_schema_extra = {
            "example": {
                "html": "<!DOCTYPE html><html><head><style>@page { margin: 2cm; }</style></head><body><h1>Test PDF</h1></body></html>",
                "filename": "test.pdf",
                "return_base64": False
            }
        }


class ConversionResponse(BaseModel):
    url: str = Field(..., description="URL naar het gegenereerde PDF bestand")
    base64: str | None = Field(None, description="Base64 encoded PDF (indien gevraagd)")
    size_kb: float = Field(..., description="Bestandsgrootte in KB")


@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "status": "online",
        "service": "HTML to PDF Converter",
        "version": "1.0.0"
    }


@app.get("/health")
async def health():
    """Health check voor Render"""
    return {"status": "healthy"}


@app.post("/convert", response_model=ConversionResponse)
async def convert_html_to_pdf(request: ConversionRequest):
    """
    Converteer HTML naar PDF met volledige CSS ondersteuning
    
    - Ondersteunt alle moderne CSS: @page, running(header), string-set, flexbox, font-face, etc.
    - Gebruikt Chromium print rendering voor perfecte output
    - UTF-8 encoding voor correcte karakters
    - Custom marges en A4 formaat
    """
    try:
        # Valideer filename
        if not request.filename.endswith('.pdf'):
            request.filename += '.pdf'
        
        # Sanitize filename
        safe_filename = "".join(c for c in request.filename if c.isalnum() or c in ('_', '-', '.'))
        output_path = OUTPUT_DIR / safe_filename
        
        logger.info(f"Starting conversion for: {safe_filename}")
        
        # Playwright initialiseren
        async with async_playwright() as p:
            # Launch Chromium browser
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-gpu',
                    '--no-first-run',
                    '--no-zygote',
                    '--single-process',
                    '--disable-extensions'
                ]
            )
            
            try:
                # Nieuwe pagina aanmaken
                page = await browser.new_page()
                
                # HTML content laden met UTF-8 encoding
                await page.set_content(
                    request.html,
                    wait_until='networkidle',
                    timeout=30000
                )
                
                # PDF genereren met volledige CSS ondersteuning
                # KRITIEK: Marges op 0 zodat HTML @page CSS gevolgd wordt
                pdf_bytes = await page.pdf(
                    path=str(output_path),
                    format='A4',
                    print_background=True,
                    prefer_css_page_size=True,
                    margin={
                        'top': '0',
                        'bottom': '0',
                        'left': '0',
                        'right': '0'
                    },
                    display_header_footer=False,
                )
                
                logger.info(f"PDF successfully generated: {safe_filename}")
                
            finally:
                await browser.close()
        
        # Bestandsgrootte bepalen
        file_size = output_path.stat().st_size / 1024  # in KB
        
        # Base URL bepalen (Render.com)
        base_url = os.getenv("RENDER_EXTERNAL_URL", "http://localhost:8000")
        pdf_url = f"{base_url}/output/{safe_filename}"
        
        response_data = {
            "url": pdf_url,
            "size_kb": round(file_size, 2)
        }
        
        # Optioneel: base64 encoding
        if request.return_base64:
            with open(output_path, 'rb') as f:
                pdf_base64 = base64.b64encode(f.read()).decode('utf-8')
                response_data["base64"] = pdf_base64
        
        return JSONResponse(content=response_data)
        
    except Exception as e:
        logger.error(f"Conversion error: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Fout bij het converteren van HTML naar PDF: {str(e)}"
        )


@app.delete("/output/{filename}")
async def delete_pdf(filename: str):
    """Verwijder een gegenereerd PDF bestand"""
    try:
        file_path = OUTPUT_DIR / filename
        if file_path.exists():
            file_path.unlink()
            return {"message": f"Bestand {filename} succesvol verwijderd"}
        else:
            raise HTTPException(status_code=404, detail="Bestand niet gevonden")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

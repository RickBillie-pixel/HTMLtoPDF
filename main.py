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
import httpx  # Voor het downloaden van de header afbeelding

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


async def get_yer_header_base64():
    """
    Download YER header afbeelding en converteer naar base64
    Dit is nodig omdat Playwright's header_template geen externe URLs ondersteunt
    """
    url = "https://vgbrkidescjeduhwhqho.supabase.co/storage/v1/object/public/cv-generated/yer-afbeelding.jpg"
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=10.0)
            response.raise_for_status()
            
            # Converteer naar base64
            image_base64 = base64.b64encode(response.content).decode('utf-8')
            return f"data:image/jpeg;base64,{image_base64}"
    except Exception as e:
        logger.error(f"Failed to download YER header image: {str(e)}")
        # Return een placeholder bij failure
        return ""


@app.post("/convert", response_model=ConversionResponse)
async def convert_html_to_pdf(request: ConversionRequest):
    """
    Converteer HTML naar PDF met automatische YER header/footer
    
    - Header en footer worden automatisch toegevoegd via Playwright
    - Header bevat YER afbeelding (wordt herhaald op elke pagina)
    - Footer bevat disclaimer (wordt herhaald op elke pagina)
    - Marges zorgen dat tekst nooit overlapt met header/footer
    - Page-break-inside: avoid werkt nu correct
    """
    try:
        # Valideer filename
        if not request.filename.endswith('.pdf'):
            request.filename += '.pdf'
        
        # Sanitize filename
        safe_filename = "".join(c for c in request.filename if c.isalnum() or c in ('_', '-', '.'))
        output_path = OUTPUT_DIR / safe_filename
        
        logger.info(f"Starting conversion for: {safe_filename}")
        
        # Download YER header afbeelding en converteer naar base64
        header_image_base64 = await get_yer_header_base64()
        
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
                
                # PDF genereren met Playwright's native header/footer
                # Dit zorgt ervoor dat Chromium exact weet waar content mag komen
                pdf_bytes = await page.pdf(
                    path=str(output_path),
                    format='A4',
                    print_background=True,
                    prefer_css_page_size=False,  # Gebruik Playwright margins, niet @page
                    
                    # Marges: header en footer ruimte
                    margin={
                        'top': '5.8cm',    # Ruimte voor YER header afbeelding
                        'bottom': '2.0cm',  # Normale bottom marge (geen footer meer)
                        'left': '2.3cm',    # Text marge links
                        'right': '2.3cm'    # Text marge rechts
                    },
                    
                    # Native header/footer (herhaalt automatisch op elke pagina)
                    display_header_footer=True,
                    
                    # Header template: YER afbeelding (base64 embedded)
                    header_template=f"""
                        <html>
                        <head>
                            <style>
                                * {{ margin: 0 !important; padding: 0 !important; }}
                                body {{ margin: 0 !important; padding: 0 !important; }}
                                div {{ margin: 0 !important; padding: 0 !important; line-height: 0 !important; }}
                                img {{ display: block !important; width: 100% !important; height: auto !important; 
                                       margin: 0 !important; padding: 0 !important; border: 0 !important; 
                                       vertical-align: top !important; }}
                            </style>
                        </head>
                        <body style='margin:0!important;padding:0!important;'>
                            <div style='width:100%;margin:0!important;padding:0!important;font-size:0;line-height:0;
                                        -webkit-print-color-adjust:exact;print-color-adjust:exact;'>
                                <img src='{header_image_base64}'
                                     style='display:block!important;width:100%!important;height:auto!important;
                                            margin:0!important;padding:0!important;border:0!important;
                                            vertical-align:top!important;'>
                            </div>
                        </body>
                        </html>
                    """,
                    
                    # Footer template: LEEG (geen footer)
                    footer_template="<div></div>",
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

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from playwright.async_api import async_playwright
from pdf2docx import Converter
import os
import base64
from pathlib import Path
import logging
import httpx
import tempfile
import asyncio
import shutil
import subprocess
from typing import Optional

# Logging configuratie
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def install_fonts_system_wide():
    """
    Install fonts from /app/fonts to system font directory
    This is CRITICAL for Playwright's Chromium to find them
    """
    try:
        # Create system font directory
        system_font_dir = Path("/usr/local/share/fonts/truetype/custom")
        system_font_dir.mkdir(parents=True, exist_ok=True)
        
        # Copy fonts from /app/fonts to system directory
        app_fonts = Path("/app/fonts")
        if app_fonts.exists():
            for font_file in app_fonts.glob("*.ttf"):
                dest = system_font_dir / font_file.name
                shutil.copy2(font_file, dest)
                os.chmod(dest, 0o644)
                logger.info(f"✓ Installed font: {font_file.name}")
        
        # Update font cache - CRITICAL STEP
        logger.info("Running fc-cache to index fonts...")
        result = subprocess.run(
            ["fc-cache", "-f", "-v"],
            capture_output=True,
            text=True
        )
        logger.info(f"fc-cache output: {result.stdout}")
        
        # Verify fonts are available
        result = subprocess.run(
            ["fc-list", ":family=Verdana"],
            capture_output=True,
            text=True
        )
        if "Verdana" in result.stdout:
            logger.info("✓✓✓ Verdana successfully installed and indexed!")
            logger.info(f"Available Verdana fonts:\n{result.stdout}")
            return True
        else:
            logger.error("✗ Verdana not found in font cache!")
            return False
            
    except Exception as e:
        logger.error(f"Failed to install fonts: {e}")
        return False


# Install fonts at startup - DO THIS BEFORE ANYTHING ELSE
logger.info("=" * 60)
logger.info("INSTALLING FONTS SYSTEM-WIDE")
logger.info("=" * 60)
install_fonts_system_wide()


app = FastAPI(
    title="HTML to PDF & PDF to Word Converter",
    description="Convert HTML to PDF and PDF to Word with full CSS support using Chromium",
    version="2.0.0"
)

# CORS configuratie voor n8n
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Output directories aanmaken
OUTPUT_DIR = Path("/app/static/output")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

WORD_OUTPUT_DIR = Path("/app/static/word_output")
WORD_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Statische files hosten
app.mount("/output", StaticFiles(directory=str(OUTPUT_DIR)), name="output")
app.mount("/word_output", StaticFiles(directory=str(WORD_OUTPUT_DIR)), name="word_output")


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


class PDFToWordRequest(BaseModel):
    pdf_base64: Optional[str] = Field(None, description="PDF bestand als base64 string")
    pdf_url: Optional[str] = Field(None, description="URL naar PDF bestand om te downloaden")
    filename: str = Field(..., description="Naam van het output Word bestand (bijv. cv_1234.docx)")
    return_base64: bool = Field(False, description="Optioneel: return Word als base64 string")
    
    class Config:
        json_schema_extra = {
            "example": {
                "pdf_url": "https://example.com/document.pdf",
                "filename": "converted_document.docx",
                "return_base64": False
            }
        }


class ConversionResponse(BaseModel):
    url: str = Field(..., description="URL naar het gegenereerde bestand")
    base64: str | None = Field(None, description="Base64 encoded bestand (indien gevraagd)")
    size_kb: float = Field(..., description="Bestandsgrootte in KB")


@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "status": "online",
        "service": "HTML to PDF & PDF to Word Converter",
        "version": "2.0.0",
        "endpoints": {
            "html_to_pdf": "/convert",
            "pdf_to_word": "/convert-pdf-to-word",
            "pdf_to_word_upload": "/convert-pdf-to-word-upload"
        }
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
                        'left': '1.0cm',    # Text marge links
                        'right': '1.0cm'    # Text marge rechts
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


async def pdf_to_word_conversion(pdf_path: Path, output_path: Path) -> None:
    """
    Voer de daadwerkelijke PDF naar Word conversie uit in een thread pool
    omdat pdf2docx geen async ondersteunt
    """
    def convert_sync():
        try:
            cv = Converter(str(pdf_path))
            cv.convert(str(output_path))
            cv.close()
        except AttributeError as e:
            # Fallback: als pdf2docx faalt, probeer een basic conversie
            if "'Rect' object has no attribute" in str(e):
                logger.warning(f"pdf2docx Rect error, trying alternative method: {e}")
                # Probeer met aangepaste settings
                cv = Converter(str(pdf_path))
                cv.convert(str(output_path), start=0, end=None)
                cv.close()
            else:
                raise
        except Exception as e:
            logger.error(f"PDF conversion failed: {str(e)}")
            raise
    
    # Run in thread pool om blocking IO te vermijden
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, convert_sync)


@app.post("/convert-pdf-to-word", response_model=ConversionResponse)
async def convert_pdf_to_word(request: PDFToWordRequest):
    """
    Converteer PDF naar Word (DOCX) formaat
    
    Accepteert:
    - pdf_base64: PDF als base64 string
    - pdf_url: URL naar een PDF bestand
    
    Returns:
    - URL naar het gegenereerde Word bestand
    - Optioneel: base64 encoded Word bestand
    """
    try:
        # Valideer dat er tenminste één input methode is
        if not request.pdf_base64 and not request.pdf_url:
            raise HTTPException(
                status_code=400,
                detail="Geef een pdf_base64 of pdf_url op"
            )
        
        # Valideer filename
        if not request.filename.endswith('.docx'):
            request.filename += '.docx'
        
        # Sanitize filename
        safe_filename = "".join(c for c in request.filename if c.isalnum() or c in ('_', '-', '.'))
        output_path = WORD_OUTPUT_DIR / safe_filename
        
        logger.info(f"Starting PDF to Word conversion for: {safe_filename}")
        
        # Tijdelijk PDF bestand aanmaken
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp_pdf:
            tmp_pdf_path = Path(tmp_pdf.name)
            
            try:
                # PDF data ophalen (base64 of URL)
                if request.pdf_base64:
                    # Decode base64
                    pdf_data = base64.b64decode(request.pdf_base64)
                    tmp_pdf.write(pdf_data)
                    logger.info("PDF loaded from base64")
                    
                elif request.pdf_url:
                    # Download PDF van URL
                    async with httpx.AsyncClient() as client:
                        response = await client.get(request.pdf_url, timeout=30.0)
                        response.raise_for_status()
                        tmp_pdf.write(response.content)
                        logger.info(f"PDF downloaded from URL: {request.pdf_url}")
                
                tmp_pdf.flush()
                
                # Converteer PDF naar Word
                await pdf_to_word_conversion(tmp_pdf_path, output_path)
                
                logger.info(f"Word document successfully generated: {safe_filename}")
                
            finally:
                # Cleanup tijdelijk PDF bestand
                if tmp_pdf_path.exists():
                    tmp_pdf_path.unlink()
        
        # Bestandsgrootte bepalen
        file_size = output_path.stat().st_size / 1024  # in KB
        
        # Base URL bepalen (Render.com)
        base_url = os.getenv("RENDER_EXTERNAL_URL", "http://localhost:8000")
        word_url = f"{base_url}/word_output/{safe_filename}"
        
        response_data = {
            "url": word_url,
            "size_kb": round(file_size, 2)
        }
        
        # Optioneel: base64 encoding
        if request.return_base64:
            with open(output_path, 'rb') as f:
                word_base64 = base64.b64encode(f.read()).decode('utf-8')
                response_data["base64"] = word_base64
        
        return JSONResponse(content=response_data)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"PDF to Word conversion error: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Fout bij het converteren van PDF naar Word: {str(e)}"
        )


@app.post("/convert-pdf-to-word-upload", response_model=ConversionResponse)
async def convert_pdf_to_word_upload(
    file: UploadFile = File(..., description="PDF bestand om te uploaden"),
    return_base64: bool = False
):
    """
    Converteer PDF naar Word via file upload
    
    Upload een PDF bestand en krijg een Word document terug
    """
    try:
        # Valideer dat het een PDF is
        if not file.filename.endswith('.pdf'):
            raise HTTPException(
                status_code=400,
                detail="Alleen PDF bestanden zijn toegestaan"
            )
        
        # Generate output filename
        base_name = file.filename.rsplit('.', 1)[0]
        safe_filename = "".join(c for c in base_name if c.isalnum() or c in ('_', '-')) + '.docx'
        output_path = WORD_OUTPUT_DIR / safe_filename
        
        logger.info(f"Starting PDF to Word conversion via upload: {file.filename} -> {safe_filename}")
        
        # Tijdelijk PDF bestand aanmaken
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp_pdf:
            tmp_pdf_path = Path(tmp_pdf.name)
            
            try:
                # Upload opslaan
                content = await file.read()
                tmp_pdf.write(content)
                tmp_pdf.flush()
                
                logger.info(f"Uploaded PDF size: {len(content) / 1024:.2f} KB")
                
                # Converteer PDF naar Word
                await pdf_to_word_conversion(tmp_pdf_path, output_path)
                
                logger.info(f"Word document successfully generated: {safe_filename}")
                
            finally:
                # Cleanup tijdelijk PDF bestand
                if tmp_pdf_path.exists():
                    tmp_pdf_path.unlink()
        
        # Bestandsgrootte bepalen
        file_size = output_path.stat().st_size / 1024  # in KB
        
        # Base URL bepalen (Render.com)
        base_url = os.getenv("RENDER_EXTERNAL_URL", "http://localhost:8000")
        word_url = f"{base_url}/word_output/{safe_filename}"
        
        response_data = {
            "url": word_url,
            "size_kb": round(file_size, 2)
        }
        
        # Optioneel: base64 encoding
        if return_base64:
            with open(output_path, 'rb') as f:
                word_base64 = base64.b64encode(f.read()).decode('utf-8')
                response_data["base64"] = word_base64
        
        return JSONResponse(content=response_data)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"PDF to Word upload conversion error: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Fout bij het converteren van PDF naar Word: {str(e)}"
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


@app.delete("/word_output/{filename}")
async def delete_word(filename: str):
    """Verwijder een gegenereerd Word bestand"""
    try:
        file_path = WORD_OUTPUT_DIR / filename
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

# main.py
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel
import httpx
import uuid
import os
from pathlib import Path

app = FastAPI(title="HTML to PDF Converter")

# Directory voor tijdelijke PDF opslag
PDF_DIR = Path("generated_pdfs")
PDF_DIR.mkdir(exist_ok=True)

# Gotenberg URL (draait in dezelfde container of via docker-compose)
GOTENBERG_URL = os.getenv("GOTENBERG_URL", "http://localhost:3000")

class HTMLRequest(BaseModel):
    html: str
    filename: str = "document.pdf"

@app.get("/")
def read_root():
    return {"message": "HTML to PDF Converter API", "status": "running"}

@app.post("/convert")
async def convert_html_to_pdf(request: HTMLRequest):
    """
    Converteer HTML naar PDF en geef download URL terug
    """
    try:
        # Genereer unieke filename
        pdf_id = str(uuid.uuid4())
        pdf_filename = f"{pdf_id}.pdf"
        pdf_path = PDF_DIR / pdf_filename
        
        # Stuur HTML naar Gotenberg
        files = {
            'files': ('index.html', request.html.encode('utf-8'), 'text/html')
        }
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{GOTENBERG_URL}/forms/chromium/convert/html",
                files=files,
                data={
                    'marginTop': '0',
                    'marginBottom': '0',
                    'marginLeft': '0',
                    'marginRight': '0',
                    'printBackground': 'true',
                }
            )
        
        if response.status_code != 200:
            raise HTTPException(
                status_code=500,
                detail=f"PDF generatie gefaald: {response.text}"
            )
        
        # Sla PDF op
        with open(pdf_path, 'wb') as f:
            f.write(response.content)
        
        # Genereer download URL
        base_url = os.getenv("BASE_URL", "http://localhost:8000")
        download_url = f"{base_url}/download/{pdf_filename}"
        
        return JSONResponse({
            "success": True,
            "pdf_id": pdf_id,
            "download_url": download_url,
            "filename": request.filename
        })
        
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="PDF generatie timeout")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/download/{pdf_filename}")
async def download_pdf(pdf_filename: str):
    """
    Download gegenereerde PDF
    """
    pdf_path = PDF_DIR / pdf_filename
    
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF niet gevonden")
    
    return FileResponse(
        path=pdf_path,
        media_type="application/pdf",
        filename=pdf_filename
    )

@app.delete("/cleanup/{pdf_filename}")
async def cleanup_pdf(pdf_filename: str):
    """
    Verwijder PDF na download (optioneel)
    """
    pdf_path = PDF_DIR / pdf_filename
    
    if pdf_path.exists():
        pdf_path.unlink()
        return {"success": True, "message": "PDF verwijderd"}
    
    raise HTTPException(status_code=404, detail="PDF niet gevonden")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

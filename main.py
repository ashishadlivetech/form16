from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
import pdfplumber
import pytesseract

from parser import Form16Parser
from validators import Form16Validator

app = FastAPI(
    title="Government Form16 Extractor",
    version="1.0.0"
)

# --------------------------------------------------
# CORS
# --------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------
# Health Check
# --------------------------------------------------
@app.get("/")
def home():
    return {
        "status": "running",
        "service": "Government Form16 Extractor"
    }


# --------------------------------------------------
# PDF Text Extraction (text layer)
# --------------------------------------------------
def extract_pdf_text(pdf):
    full_text = ""
    for page in pdf.pages:
        try:
            page_text = page.extract_text()
            if page_text:
                full_text += "\n" + page_text
        except Exception:
            continue
    return full_text


# --------------------------------------------------
# OCR Fallback (scanned / image-only PDFs)
# --------------------------------------------------
def extract_pdf_text_via_ocr(pdf, resolution=400):
    full_text = ""
    ocr_error = None

    for page in pdf.pages:
        try:
            image = page.to_image(resolution=resolution).original
            page_text = pytesseract.image_to_string(image, config="--psm 6")
            if page_text:
                full_text += "\n" + page_text
        except pytesseract.TesseractNotFoundError as e:
            # The tesseract binary itself isn't installed on this machine.
            # pytesseract only wraps the CLI tool - it does not bundle it.
            ocr_error = (
                "Tesseract OCR engine is not installed on this server. "
                "Install it with 'apt-get install -y tesseract-ocr' "
                "(Linux) or 'brew install tesseract' (Mac), then restart "
                f"the server. Original error: {e}"
            )
            break
        except Exception as e:
            ocr_error = f"OCR failed on a page: {e}"
            # Keep trying remaining pages rather than aborting entirely.
            continue

    return full_text, ocr_error


# --------------------------------------------------
# Extract API
# --------------------------------------------------
@app.post("/extract")
async def extract(file: UploadFile = File(...)):
    try:
        ocr_used = False
        ocr_error = None

        with pdfplumber.open(file.file) as pdf:
            text = extract_pdf_text(pdf)

            # If the PDF has no embedded text layer (a scanned image
            # rendered into the PDF rather than real text), fall back to
            # OCR by rasterizing each page and running tesseract on it.
            if not text.strip():
                text, ocr_error = extract_pdf_text_via_ocr(pdf)
                ocr_used = True

        if not text.strip():
            if ocr_error:
                return {
                    "success": False,
                    "message": "No readable text found in PDF. OCR fallback "
                                "also failed.",
                    "error": ocr_error
                }
            return {
                "success": False,
                "message": "No readable text found in PDF, even after OCR. "
                            "The scan quality may be too low to process."
            }

        # --------------------------
        # Parse
        # --------------------------
        parser = Form16Parser(text)
        parsed_data = parser.parse()

        # --------------------------
        # Validate
        # --------------------------
        validator = Form16Validator(parsed_data)
        validation_result = validator.validate()

        if ocr_used:
            validation_result.setdefault("warnings", []).append(
                "Text was extracted via OCR (scanned PDF). Some characters "
                "(e.g. similar-looking letters/digits in PAN/TAN) may be "
                "misread — please double check those fields."
            )

        # --------------------------
        # Final Response
        # --------------------------
        return {
            "success": True,
            "filename": file.filename,
            "ocrUsed": ocr_used,
            "documentType": parsed_data.get("documentType"),
            "confidence": validation_result.get("confidence"),
            "validation": validation_result,
            "dynamicFields": parsed_data.get("dynamicFields", {}),
            "structuredData": parsed_data.get("structuredData", {})
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


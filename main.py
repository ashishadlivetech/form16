from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
import pdfplumber
import ocrmypdf
import io

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
# Step 1 — Check if PDF has a real embedded text layer
# --------------------------------------------------
def is_text_based_pdf(file_bytes: bytes) -> bool:
    """
    Returns True when the PDF already has a searchable text layer
    (i.e. pdfplumber can extract characters from it).
    Returns False when every page is a scanned / rendered image
    with zero extractable characters — meaning OCR conversion is needed.
    """
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            if page.chars:           # at least one text character found
                return True
    return False


# --------------------------------------------------
# Step 2a — Extract text from a real text-based PDF
# --------------------------------------------------
def extract_text_from_pdf(file_bytes: bytes) -> str:
    full_text = ""
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            try:
                page_text = page.extract_text()
                if page_text:
                    full_text += "\n" + page_text
            except Exception:
                continue
    return full_text


# --------------------------------------------------
# Step 2b — Convert scanned PDF → searchable PDF → extract text
#
# ocrmypdf runs Tesseract on each page image and writes an invisible
# text layer over the original page, producing a proper searchable PDF
# in memory (output_buf).  pdfplumber then reads that new PDF normally,
# giving us clean, high-accuracy text — no character ambiguity from raw
# Tesseract output.
# --------------------------------------------------
def convert_scanned_pdf_and_extract(file_bytes: bytes):
    """
    Converts a scanned / image-only PDF to a searchable PDF using
    ocrmypdf, then extracts text with pdfplumber.

    Returns (text: str, error: str | None).
    """
    output_buf = io.BytesIO()

    try:
        ocrmypdf.ocr(
            io.BytesIO(file_bytes),
            output_buf,
            language="eng",
            deskew=True,       # auto-straighten skewed scans
            optimize=0,        # skip PDF compression (faster, no quality loss)
            progress_bar=False
        )
    except ocrmypdf.exceptions.PriorOcrFoundError:
        # PDF already has an OCR layer but pdfplumber still couldn't read it
        # (rare: corrupted or non-standard encoding). Try forcing a redo.
        output_buf = io.BytesIO()
        try:
            ocrmypdf.ocr(
                io.BytesIO(file_bytes),
                output_buf,
                language="eng",
                redo_ocr=True,
                optimize=0,
                progress_bar=False
            )
        except Exception as e:
            return "", f"ocrmypdf failed even with redo_ocr: {e}"
    except ocrmypdf.exceptions.MissingDependencyError as e:
        return "", (
            "A system dependency required for OCR conversion is missing. "
            "Make sure your Docker image installs: "
            "tesseract-ocr, tesseract-ocr-eng, poppler-utils, pngquant. "
            f"Details: {e}"
        )
    except Exception as e:
        return "", f"ocrmypdf conversion failed: {e}"

    output_buf.seek(0)
    text = extract_text_from_pdf(output_buf.read())
    return text, None


# --------------------------------------------------
# Extract API
# --------------------------------------------------
@app.post("/extract")
async def extract(file: UploadFile = File(...)):
    try:
        file_bytes = await file.read()
        ocr_used = False

        # ── Step 1: detect PDF type ──────────────────────────────────────
        if is_text_based_pdf(file_bytes):
            # Real text-layer PDF — extract directly, fast path.
            pdf_type = "text-based"
            text = extract_text_from_pdf(file_bytes)
        else:
            # Scanned / image-only PDF — convert first, then extract.
            pdf_type = "scanned"
            text, ocr_error = convert_scanned_pdf_and_extract(file_bytes)
            ocr_used = True

            if not text.strip():
                return {
                    "success": False,
                    "pdfType": pdf_type,
                    "message": "PDF was detected as scanned/image-only. "
                               "OCR conversion was attempted but produced no text.",
                    "error": ocr_error or "No text extracted after OCR."
                }

        if not text.strip():
            return {
                "success": False,
                "pdfType": pdf_type,
                "message": "PDF has a text layer but no readable text could be extracted."
            }

        # ── Step 2: Parse ────────────────────────────────────────────────
        parser = Form16Parser(text)
        parsed_data = parser.parse()

        # ── Step 3: Validate ─────────────────────────────────────────────
        validator = Form16Validator(parsed_data)
        validation_result = validator.validate()

        # ── Step 4: Respond ──────────────────────────────────────────────
        return {
            "success": True,
            "filename": file.filename,
            "pdfType": pdf_type,
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

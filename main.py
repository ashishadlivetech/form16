from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
import pdfplumber
import pytesseract
import io
from pdf2image import convert_from_bytes

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
def extract_pdf_text_via_ocr(file_bytes):
    full_text = ""

    try:
        images = convert_from_bytes(file_bytes, dpi=300)

        print(f"Total OCR pages: {len(images)}")

        for index, image in enumerate(images):
            print(f"Running OCR on page {index + 1}")

            page_text = pytesseract.image_to_string(
                image,
                config="--oem 3 --psm 6"
            )

            full_text += "\n" + page_text

        return full_text, None

    except pytesseract.TesseractNotFoundError as e:
        return "", f"Tesseract not installed: {e}"

    except Exception as e:
        return "", str(e)
# --------------------------------------------------
# Extract API
# --------------------------------------------------
@app.post("/extract")
async def extract(file: UploadFile = File(...)):
    try:
        ocr_used = False
        ocr_error = None

        file_bytes = await file.read()

        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            text = extract_pdf_text(pdf)
        
        if not text.strip():
            print("No text layer found. Starting OCR...")
            text, ocr_error = extract_pdf_text_via_ocr(file_bytes)
            ocr_used = True

        if not text.strip():
            if ocr_error:
                return {
                    "success": False,
                    "message": "No readable text found in PDF. OCR fallback "
                                "also failed.",
                    "error": ocr_error
                }
                print("Extracted text length:", len(text))
                print("OCR Used:", ocr_used)
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


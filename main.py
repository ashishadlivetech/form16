import gc
import logging
import time
import traceback
import io

import pdfplumber
import pytesseract
from pdf2image import convert_from_bytes
from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware

from parser import Form16Parser
from validators import Form16Validator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("form16")

app = FastAPI(title="Government Form16 Extractor", version="1.0.0")

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
    return {"status": "running", "service": "Government Form16 Extractor"}


# --------------------------------------------------
# Debug
# --------------------------------------------------
@app.get("/debug")
def debug():
    import shutil, subprocess, sys, platform

    results = {}

    pkg_status = {}
    for pkg in ["pdfplumber", "pytesseract", "pdf2image", "PIL"]:
        try:
            mod = __import__(pkg)
            pkg_status[pkg] = f"OK - {getattr(mod, '__version__', 'installed')}"
        except ImportError as e:
            pkg_status[pkg] = f"MISSING - {e}"
    results["python_packages"] = pkg_status

    bin_status = {}
    for binary in ["tesseract", "pdftoppm", "gs", "pngquant"]:
        path = shutil.which(binary)
        bin_status[binary] = f"OK - {path}" if path else "MISSING"
    results["system_binaries"] = bin_status

    if shutil.which("tesseract"):
        try:
            langs = subprocess.check_output(
                ["tesseract", "--list-langs"], stderr=subprocess.STDOUT
            ).decode().strip()
            results["tesseract_languages"] = langs
            results["tesseract_eng_available"] = "eng" in langs
        except Exception as e:
            results["tesseract_languages"] = f"ERROR - {e}"
    else:
        results["tesseract_eng_available"] = False

    missing = []
    if "MISSING" in bin_status.get("tesseract", ""):
        missing.append("tesseract-ocr")
    if not results.get("tesseract_eng_available", True):
        missing.append("tesseract-ocr-eng")
    if "MISSING" in bin_status.get("pdftoppm", ""):
        missing.append("poppler-utils")

    results["verdict"] = "ALL OK" if not missing else f"BROKEN - missing: {', '.join(missing)}"
    results["platform"] = platform.platform()
    results["python_version"] = sys.version
    return results


# --------------------------------------------------
# Step 1 — Check if PDF has a real text layer
# --------------------------------------------------
def is_text_based_pdf(file_bytes: bytes) -> bool:
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            if page.chars:
                return True
    return False


# --------------------------------------------------
# Step 2a — Extract text from text-based PDF
# --------------------------------------------------
def extract_text_from_pdf(file_bytes: bytes) -> str:
    full_text = ""
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for i, page in enumerate(pdf.pages):
            try:
                page_text = page.extract_text()
                if page_text:
                    full_text += "\n" + page_text
                    log.info("[TEXT-EXTRACT] Page %d → %d chars", i + 1, len(page_text))
                else:
                    log.warning("[TEXT-EXTRACT] Page %d → no text", i + 1)
            except Exception as e:
                log.error("[TEXT-EXTRACT] Page %d error: %s", i + 1, e)
    log.info("[TEXT-EXTRACT] Total: %d chars", len(full_text))
    return full_text


# --------------------------------------------------
# Step 2b — OCR scanned PDF page by page at 200 DPI
#
# Why 200 DPI and not higher:
#   150 DPI → ~93MB RAM but misses small text (PAN/TAN)
#   200 DPI → ~102MB RAM, catches everything on Form 16
#   300 DPI → ~230MB RAM, no accuracy gain on Form 16
#   400 DPI → ~410MB RAM, crashes Render 512MB free tier
#
# Pages are processed one at a time and deleted immediately
# after OCR so peak RAM stays under 120MB total.
# --------------------------------------------------
def extract_text_via_ocr(file_bytes: bytes):
    """
    Returns (text: str, error: str | None)
    """
    log.info("[OCR] Starting page-by-page OCR at 200 DPI...")
    t_start = time.time()

    try:
        pages = convert_from_bytes(file_bytes, dpi=200)
    except Exception as e:
        log.error("[OCR] convert_from_bytes failed: %s", e)
        return "", f"PDF to image conversion failed: {e}"

    log.info("[OCR] Converted %d pages to images", len(pages))

    full_text = ""
    for i, page_image in enumerate(pages):
        try:
            page_text = pytesseract.image_to_string(page_image, config="--psm 6")
            full_text += page_text
            log.info("[OCR] Page %d → %d chars", i + 1, len(page_text))
        except Exception as e:
            log.error("[OCR] Page %d OCR failed: %s", i + 1, e)
        finally:
            del page_image   # free image memory immediately
            gc.collect()

    del pages
    gc.collect()

    elapsed = time.time() - t_start
    log.info("[OCR] Done in %.1fs — total %d chars extracted", elapsed, len(full_text))

    if not full_text.strip():
        return "", "OCR ran but produced no text. Check tesseract-ocr-eng is installed."

    return full_text, None


# --------------------------------------------------
# Extract API
# --------------------------------------------------
@app.post("/extract")
async def extract(file: UploadFile = File(...)):
    request_start = time.time()
    log.info("=" * 50)
    log.info("[REQUEST] filename=%s", file.filename)

    try:
        # Step 1 — Read file
        log.info("[STEP 1/4] Reading file...")
        file_bytes = await file.read()
        log.info("[STEP 1/4] Size: %.1f KB", len(file_bytes) / 1024)

        if not file_bytes:
            return {"success": False, "error": "Uploaded file is empty."}

        # Step 2 — Detect type and extract text
        log.info("[STEP 2/4] Detecting PDF type...")
        ocr_used = False

        try:
            is_text = is_text_based_pdf(file_bytes)
        except Exception as e:
            log.error("[STEP 2/4] PDF open failed: %s\n%s", e, traceback.format_exc())
            return {"success": False, "error": f"Could not open PDF: {e}"}

        if is_text:
            pdf_type = "text-based"
            log.info("[STEP 2/4] text-based PDF → direct extraction")
            try:
                text = extract_text_from_pdf(file_bytes)
            except Exception as e:
                log.error("[STEP 2/4] Text extraction failed: %s", e)
                return {"success": False, "error": f"Text extraction failed: {e}"}
        else:
            pdf_type = "scanned"
            ocr_used = True
            log.info("[STEP 2/4] scanned PDF → OCR")
            try:
                text, ocr_error = extract_text_via_ocr(file_bytes)
            except Exception as e:
                log.error("[STEP 2/4] OCR crashed: %s\n%s", e, traceback.format_exc())
                return {"success": False, "error": f"OCR crashed: {e}"}

            if not text.strip():
                return {
                    "success": False,
                    "pdfType": pdf_type,
                    "error": ocr_error or "No text extracted after OCR."
                }

        if not text.strip():
            return {
                "success": False,
                "pdfType": pdf_type,
                "error": "No readable text found in PDF."
            }

        log.info("[STEP 2/4] Extracted %d chars", len(text))

        # Step 3 — Parse
        log.info("[STEP 3/4] Parsing...")
        try:
            parser = Form16Parser(text)
            parsed_data = parser.parse()
            log.info("[STEP 3/4] documentType=%s confidence=%s",
                     parsed_data.get("documentType"), parsed_data.get("confidence"))
        except Exception as e:
            log.error("[STEP 3/4] Parser failed: %s\n%s", e, traceback.format_exc())
            return {"success": False, "error": f"Parser failed: {e}"}

        # Step 4 — Validate
        log.info("[STEP 4/4] Validating...")
        try:
            validator = Form16Validator(parsed_data)
            validation_result = validator.validate()
            log.info("[STEP 4/4] isValid=%s warnings=%s",
                     validation_result.get("isValid"), validation_result.get("warnings"))
        except Exception as e:
            log.error("[STEP 4/4] Validator failed: %s\n%s", e, traceback.format_exc())
            return {"success": False, "error": f"Validator failed: {e}"}

        elapsed = time.time() - request_start
        log.info("[DONE] Completed in %.1fs", elapsed)

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
        elapsed = time.time() - request_start
        log.error("[FATAL] %.1fs: %s\n%s", elapsed, e, traceback.format_exc())
        return {
            "success": False,
            "error": str(e),
            "trace": traceback.format_exc()
        }

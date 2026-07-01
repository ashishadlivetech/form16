import logging
import time
import traceback
import io

import ocrmypdf
import pdfplumber
from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware

from parser import Form16Parser
from validators import Form16Validator

# --------------------------------------------------
# Logging setup
# Every log line includes: timestamp | level | step | message
# View these in your hosting platform's log console
# (Render → Logs tab, Railway → Deployments → Logs, etc.)
# --------------------------------------------------
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("form16")


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
# Debug endpoint — hit this first when something breaks
# GET /debug  → checks every system dependency at runtime
# --------------------------------------------------
@app.get("/debug")
def debug():
    import shutil
    import subprocess
    import sys

    results = {}

    # 1. Python packages
    python_packages = ["pdfplumber", "ocrmypdf", "PIL"]
    pkg_status = {}
    for pkg in python_packages:
        try:
            mod = __import__(pkg)
            version = getattr(mod, "__version__", "installed")
            pkg_status[pkg] = f"OK - {version}"
        except ImportError as e:
            pkg_status[pkg] = f"MISSING - {e}"
    results["python_packages"] = pkg_status

    # 2. System binaries
    binaries = ["tesseract", "pdftoppm", "pdfinfo", "gs", "pngquant"]
    bin_status = {}
    for binary in binaries:
        path = shutil.which(binary)
        bin_status[binary] = f"OK - {path}" if path else "MISSING - not found in PATH"
    results["system_binaries"] = bin_status

    # 3. Tesseract version + language packs
    if shutil.which("tesseract"):
        try:
            ver = subprocess.check_output(
                ["tesseract", "--version"], stderr=subprocess.STDOUT
            ).decode().strip().split("\n")[0]
            results["tesseract_version"] = ver
            langs = subprocess.check_output(
                ["tesseract", "--list-langs"], stderr=subprocess.STDOUT
            ).decode().strip()
            results["tesseract_languages"] = langs
            results["tesseract_eng_available"] = "eng" in langs
        except Exception as e:
            results["tesseract_check_error"] = str(e)
    else:
        results["tesseract_version"] = "MISSING"
        results["tesseract_eng_available"] = False

    # 4. Poppler version
    if shutil.which("pdftoppm"):
        try:
            ver = subprocess.check_output(
                ["pdftoppm", "-v"], stderr=subprocess.STDOUT
            ).decode().strip().split("\n")[0]
            results["poppler_version"] = ver
        except Exception as e:
            results["poppler_version"] = f"found but version check failed: {e}"
    else:
        results["poppler_version"] = "MISSING"

    # 5. Environment
    import platform
    results["python_version"] = sys.version
    results["platform"] = platform.platform()

    # 6. Verdict
    critical_missing = []
    if "MISSING" in bin_status.get("tesseract", ""):
        critical_missing.append("tesseract (apt: tesseract-ocr)")
    if not results.get("tesseract_eng_available", True):
        critical_missing.append("tesseract-eng (apt: tesseract-ocr-eng)")
    if "MISSING" in bin_status.get("pdftoppm", ""):
        critical_missing.append("poppler-utils (apt: poppler-utils)")
    if "MISSING" in bin_status.get("gs", ""):
        critical_missing.append("ghostscript (apt: ghostscript)")

    results["verdict"] = (
        "ALL OK - scanned PDF processing should work"
        if not critical_missing
        else "BROKEN - missing: " + ", ".join(critical_missing)
    )

    log.info("[DEBUG] verdict=%s", results["verdict"])
    return results


# --------------------------------------------------
# Step 1 — Check if PDF has a real embedded text layer
# --------------------------------------------------
def is_text_based_pdf(file_bytes: bytes) -> bool:
    """
    Returns True when the PDF already has a searchable text layer.
    Returns False when every page is a scanned / rendered image.
    """
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        total_pages = len(pdf.pages)
        log.debug("[PDF-CHECK] Total pages in PDF: %d", total_pages)

        for i, page in enumerate(pdf.pages):
            char_count = len(page.chars)
            log.debug("[PDF-CHECK] Page %d → %d characters found", i + 1, char_count)
            if page.chars:
                log.info("[PDF-CHECK] Text layer detected on page %d — PDF is text-based", i + 1)
                return True

    log.info("[PDF-CHECK] No text characters found on any page — PDF is scanned/image-only")
    return False


# --------------------------------------------------
# Step 2a — Extract text from a real text-based PDF
# --------------------------------------------------
def extract_text_from_pdf(file_bytes: bytes) -> str:
    full_text = ""

    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for i, page in enumerate(pdf.pages):
            try:
                page_text = page.extract_text()
                char_count = len(page_text) if page_text else 0
                log.debug("[TEXT-EXTRACT] Page %d → %d chars extracted", i + 1, char_count)

                if page_text:
                    full_text += "\n" + page_text
                else:
                    log.warning("[TEXT-EXTRACT] Page %d returned no text", i + 1)

            except Exception as e:
                log.error("[TEXT-EXTRACT] Page %d raised an exception: %s", i + 1, e)
                continue

    log.info("[TEXT-EXTRACT] Total text length: %d chars", len(full_text))
    return full_text


# --------------------------------------------------
# Step 2b — Convert scanned PDF → searchable PDF → extract text
# --------------------------------------------------
def convert_scanned_pdf_and_extract(file_bytes: bytes):
    """
    Converts a scanned / image-only PDF to a searchable PDF using
    ocrmypdf, then extracts text with pdfplumber.
    Returns (text: str, error: str | None).
    """
    output_buf = io.BytesIO()
    t_start = time.time()

    log.info("[OCR] Starting ocrmypdf conversion...")

    # Memory-saving settings for low-RAM servers (512MB Render free/starter):
    #   deskew=False  → skipping deskew saves ~600MB RAM (biggest saving)
    #   jobs=1        → one page at a time, not all pages in parallel
    #   optimize=0    → skip image compression (saves RAM + time)
    OCR_OPTS = dict(
        language="eng",
        deskew=False,
        jobs=1,
        optimize=0,
        jpeg_quality=70,
        progress_bar=False,
    )

    try:
        ocrmypdf.ocr(io.BytesIO(file_bytes), output_buf, **OCR_OPTS)
        log.info("[OCR] ocrmypdf finished in %.2fs", time.time() - t_start)

    except ocrmypdf.exceptions.PriorOcrFoundError:
        log.warning("[OCR] PDF already has an OCR layer — retrying with redo_ocr=True")
        output_buf = io.BytesIO()
        try:
            ocrmypdf.ocr(io.BytesIO(file_bytes), output_buf, redo_ocr=True, **OCR_OPTS)
            log.info("[OCR] redo_ocr finished in %.2fs", time.time() - t_start)
        except Exception as e:
            log.error("[OCR] redo_ocr also failed: %s\n%s", e, traceback.format_exc())
            return "", f"ocrmypdf failed even with redo_ocr: {e}"

    except ocrmypdf.exceptions.MissingDependencyError as e:
        msg = (
            "A system dependency required for OCR is missing on this server. "
            "The Dockerfile must install: tesseract-ocr, tesseract-ocr-eng, "
            f"poppler-utils, pngquant, ghostscript. Details: {e}"
        )
        log.error("[OCR] MissingDependencyError: %s", msg)
        return "", msg

    except Exception as e:
        log.error("[OCR] Unexpected error: %s\n%s", e, traceback.format_exc())
        return "", f"ocrmypdf conversion failed: {e}"

    output_buf.seek(0)
    log.info("[OCR] Extracting text from converted PDF...")
    text = extract_text_from_pdf(output_buf.read())

    if not text.strip():
        log.warning("[OCR] Conversion succeeded but extracted text is empty")
    else:
        log.info("[OCR] Successfully extracted %d chars from converted PDF", len(text))

    return text, None


# --------------------------------------------------
# Extract API
# --------------------------------------------------
@app.post("/extract")
async def extract(file: UploadFile = File(...)):
    request_start = time.time()
    log.info("=" * 60)
    log.info("[REQUEST] New upload: filename=%s, content_type=%s",
             file.filename, file.content_type)

    try:
        # ── Read file ────────────────────────────────────────────────
        log.info("[STEP 1/4] Reading uploaded file...")
        file_bytes = await file.read()
        log.info("[STEP 1/4] File size: %d bytes (%.1f KB)", len(file_bytes), len(file_bytes) / 1024)

        if not file_bytes:
            log.error("[STEP 1/4] File is empty — aborting")
            return {"success": False, "error": "Uploaded file is empty."}

        # ── Detect PDF type ──────────────────────────────────────────
        log.info("[STEP 2/4] Detecting PDF type (text-based vs scanned)...")
        ocr_used = False

        try:
            is_text = is_text_based_pdf(file_bytes)
        except Exception as e:
            log.error("[STEP 2/4] PDF type detection crashed: %s\n%s", e, traceback.format_exc())
            return {"success": False, "error": f"Could not open PDF: {e}"}

        if is_text:
            pdf_type = "text-based"
            log.info("[STEP 2/4] PDF type = text-based → extracting text directly")

            try:
                text = extract_text_from_pdf(file_bytes)
            except Exception as e:
                log.error("[STEP 2/4] Text extraction crashed: %s\n%s", e, traceback.format_exc())
                return {"success": False, "error": f"Text extraction failed: {e}"}

        else:
            pdf_type = "scanned"
            ocr_used = True
            log.info("[STEP 2/4] PDF type = scanned → running OCR conversion")

            try:
                text, ocr_error = convert_scanned_pdf_and_extract(file_bytes)
            except Exception as e:
                log.error("[STEP 2/4] OCR conversion crashed: %s\n%s", e, traceback.format_exc())
                return {"success": False, "error": f"OCR conversion crashed: {e}"}

            if not text.strip():
                log.error("[STEP 2/4] OCR produced no text. ocr_error=%s", ocr_error)
                return {
                    "success": False,
                    "pdfType": pdf_type,
                    "message": "PDF is scanned/image-only. OCR ran but produced no text.",
                    "error": ocr_error or "No text extracted after OCR."
                }

        if not text.strip():
            log.error("[STEP 2/4] Final text is empty for a text-based PDF")
            return {
                "success": False,
                "pdfType": pdf_type,
                "message": "PDF has a text layer but no readable text could be extracted."
            }

        log.info("[STEP 2/4] Text extraction complete: %d chars", len(text))

        # ── Parse ────────────────────────────────────────────────────
        log.info("[STEP 3/4] Parsing extracted text with Form16Parser...")
        try:
            parser = Form16Parser(text)
            parsed_data = parser.parse()
            log.info("[STEP 3/4] Parsed. documentType=%s, confidence=%s",
                     parsed_data.get("documentType"), parsed_data.get("confidence"))
            log.debug("[STEP 3/4] dynamicFields=%s", parsed_data.get("dynamicFields"))
        except Exception as e:
            log.error("[STEP 3/4] Parser crashed: %s\n%s", e, traceback.format_exc())
            return {"success": False, "error": f"Parser failed: {e}"}

        # ── Validate ─────────────────────────────────────────────────
        log.info("[STEP 4/4] Running validator...")
        try:
            validator = Form16Validator(parsed_data)
            validation_result = validator.validate()
            log.info("[STEP 4/4] Validation done. isValid=%s, warnings=%s",
                     validation_result.get("isValid"), validation_result.get("warnings"))
        except Exception as e:
            log.error("[STEP 4/4] Validator crashed: %s\n%s", e, traceback.format_exc())
            return {"success": False, "error": f"Validator failed: {e}"}

        # ── Respond ──────────────────────────────────────────────────
        elapsed = time.time() - request_start
        log.info("[DONE] Request completed in %.2fs — success=True", elapsed)

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
        log.error("[FATAL] Unhandled exception after %.2fs: %s\n%s",
                  elapsed, e, traceback.format_exc())
        return {
            "success": False,
            "error": str(e),
            "trace": traceback.format_exc()
        }

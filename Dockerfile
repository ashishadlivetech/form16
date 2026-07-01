FROM python:3.11-slim

# -------------------------------------------------------
# System packages required:
#
#   tesseract-ocr        → OCR engine that ocrmypdf uses internally
#   tesseract-ocr-eng    → English language model for tesseract
#   poppler-utils        → pdfplumber page rendering + ocrmypdf PDF ops
#   pngquant             → used by ocrmypdf for image optimisation
#   libglib2.0-0         → required by Pillow in headless environments
#   ghostscript          → required by ocrmypdf for PDF/A output
#
# NOTE: pytesseract is NOT needed — ocrmypdf calls tesseract directly.
# -------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    poppler-utils \
    pngquant \
    ghostscript \
    libglib2.0-0 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 10000

# Use 2 workers; timeout 300s to allow OCR on large scanned PDFs
CMD ["gunicorn", "main:app", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--workers", "2", \
     "--bind", "0.0.0.0:10000", \
     "--timeout", "300"]

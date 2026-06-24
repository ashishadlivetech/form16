from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
import pdfplumber

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
    allow_origins=[
        "*"
    ],
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
# PDF Text Extraction
# --------------------------------------------------

def extract_pdf_text(file_obj):

    full_text = ""

    with pdfplumber.open(file_obj) as pdf:

        for page in pdf.pages:

            try:

                page_text = page.extract_text()

                if page_text:
                    full_text += "\n" + page_text

            except Exception:
                continue

    return full_text

# --------------------------------------------------
# Extract API
# --------------------------------------------------

@app.post("/extract")
async def extract(file: UploadFile = File(...)):

    try:

        text = extract_pdf_text(file.file)

        if not text.strip():

            return {
                "success": False,
                "message": "No readable text found in PDF"
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

        # --------------------------
        # Final Response
        # --------------------------

        return {

            "success": True,

            "filename": file.filename,

            "documentType":
                parsed_data.get(
                    "documentType"
                ),

            "confidence":
                validation_result.get(
                    "confidence"
                ),

            "validation":
                validation_result,

            "dynamicFields":
                parsed_data.get(
                    "dynamicFields",
                    {}
                ),

            "structuredData":
                parsed_data.get(
                    "structuredData",
                    {}
                )
        }

    except Exception as e:

        return {
            "success": False,
            "error": str(e)
        }
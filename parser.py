import re


class Form16Parser:

    def __init__(self, text: str):
        self.text = text

    # --------------------------------------------------
    # Utilities
    # --------------------------------------------------

    def _search(self, pattern):
        match = re.search(
            pattern,
            self.text,
            re.IGNORECASE | re.MULTILINE | re.DOTALL
        )

        if match:
            return match.group(1).strip()

        return ""

    def _number(self, pattern):

        value = self._search(pattern)

        if not value:
            return 0

        value = value.replace(",", "")

        try:
            return float(value)
        except:
            return 0

    # --------------------------------------------------
    # Document Type Detection
    # --------------------------------------------------

    def detect_document_type(self):

        text = self.text.lower()

        if "summary of amount paid/credited and tax deducted" in text:
            return "FORM16_PART_A"

        if "details of salary paid and any other income" in text:
            return "FORM16_PART_B"

        if "form no. 16" in text:
            return "FORM16"

        return "UNKNOWN"

    # --------------------------------------------------
    # Dynamic Extraction
    # --------------------------------------------------

    def extract_dynamic_fields(self):

        fields = {}

        patterns = {

            "Employer Name":
                r"Name and address of the Employer.*?\n([A-Z][A-Z\s&.,\-]+)",

            "Employee Name":
                r"Name and address of the Employee.*?\n([A-Z][A-Z\s]+)",

            "PAN of Deductor":
                r"PAN of the Deductor\s+([A-Z]{5}[0-9]{4}[A-Z])",

            "TAN of Deductor":
                r"TAN of the Deductor\s+([A-Z0-9]+)",

            "PAN of Employee":
                r"PAN of the Employee.*?([A-Z]{5}[0-9]{4}[A-Z])",

            "Assessment Year":
                r"Assessment Year\s+([0-9\-]+)",

            "Period From":
                r"From\s+([0-9A-Za-z\-]+)",

            "Period To":
                r"To\s+([0-9A-Za-z\-]+)"
        }

        for key, pattern in patterns.items():

            value = self._search(pattern)

            if value:
                fields[key] = value

        return fields

    # --------------------------------------------------
    # Salary Section
    # --------------------------------------------------

    def extract_salary(self):

        return {

            "salary17_1":
                self._number(
                    r"Salary as per provisions contained in section 17\(1\)\s+([\d.]+)"
                ),

            "perquisites17_2":
                self._number(
                    r"section 17\(2\).*?([\d.]+)"
                ),

            "profits17_3":
                self._number(
                    r"section 17\(3\).*?([\d.]+)"
                ),

            "standardDeduction":
                self._number(
                    r"Standard deduction under section 16\(ia\)\s+([\d.]+)"
                ),

            "incomeChargeableSalaries":
                self._number(
                    r'Income chargeable under the head "Salaries".*?([\d.]+)'
                )
        }

    # --------------------------------------------------
    # Tax Section
    # --------------------------------------------------

    def extract_taxes(self):

        return {

            "taxOnIncome":
                self._number(
                    r"Tax on total income\s+([\d.]+)"
                ),

            "healthEducationCess":
                self._number(
                    r"Health and education cess\s+([\d.]+)"
                ),

            "taxPayable":
                self._number(
                    r"Tax payable.*?([\d.]+)"
                ),

            "netTaxPayable":
                self._number(
                    r"Net tax payable.*?([\d.]+)"
                )
        }

    # --------------------------------------------------
    # Structured JSON
    # --------------------------------------------------

    def extract_structured(self):

        employee_name = self._search(
            r"Name and address of the Employee.*?\n([A-Z][A-Z\s]+)"
        )

        employer_name = self._search(
            r"Name and address of the Employer.*?\n([A-Z][A-Z\s&.,\-]+)"
        )

        structured = {

            "employee": {

                "name": employee_name,

                "pan":
                    self._search(
                        r"PAN of the Employee.*?([A-Z]{5}[0-9]{4}[A-Z])"
                    )
            },

            "employer": {

                "name": employer_name,

                "pan":
                    self._search(
                        r"PAN of the Deductor\s+([A-Z]{5}[0-9]{4}[A-Z])"
                    ),

                "tan":
                    self._search(
                        r"TAN of the Deductor\s+([A-Z0-9]+)"
                    )
            },

            "assessment": {

                "assessmentYear":
                    self._search(
                        r"Assessment Year\s+([0-9\-]+)"
                    ),

                "periodFrom":
                    self._search(
                        r"From\s+([0-9A-Za-z\-]+)"
                    ),

                "periodTo":
                    self._search(
                        r"To\s+([0-9A-Za-z\-]+)"
                    )
            },

            "salary":
                self.extract_salary(),

            "taxes":
                self.extract_taxes()
        }

        return structured

    # --------------------------------------------------
    # Confidence
    # --------------------------------------------------

    def confidence(self):

        score = 0

        checks = [

            r"Assessment Year",
            r"PAN of the Employee",
            r"TAN of the Deductor",
            r"Name and address of the Employer",
            r"Name and address of the Employee"
        ]

        for item in checks:

            if re.search(item, self.text, re.IGNORECASE):
                score += 20

        return score

    # --------------------------------------------------
    # Final Parse
    # --------------------------------------------------

    def parse(self):

        return {

            "documentType":
                self.detect_document_type(),

            "confidence":
                self.confidence(),

            "dynamicFields":
                self.extract_dynamic_fields(),

            "structuredData":
                self.extract_structured()
        }
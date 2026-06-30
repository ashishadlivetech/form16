import re


class Form16Parser:

    def __init__(self, text: str):
        self.text = text

    # --------------------------------------------------
    # Utilities
    # --------------------------------------------------

    def _search(self, pattern, group=1):
        match = re.search(
            pattern,
            self.text,
            re.IGNORECASE | re.MULTILINE
        )

        if match:
            return match.group(group).strip()

        return ""

    def _number(self, pattern, group=1):

        value = self._search(pattern, group)

        if not value:
            return 0

        value = value.replace(",", "")

        try:
            return float(value)
        except Exception:
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

            # Employer name sits on its own line, right after the
            # "Name and address of the Employer..." header line.
            "Employer Name":
                r"Name and address of the Employer.*\n([A-Z][A-Z0-9 &.,\-]+)",

            # The employee name sits on the same physical line as the
            # employer's PIN code (the two side-by-side table columns get
            # flattened onto one text line by pdfplumber), right before the
            # employee's address line which starts with a house/door number.
            "Employee Name":
                r"-\s*\d{6}\s+([A-Z][A-Za-z.]+(?:\s[A-Z][A-Za-z.]+)+)\n",

            "Assessment Year":
                r"Assessment Year[:\s]+([0-9]{4}-[0-9]{2})",

            "Period From":
                r"\b(\d{2}-[A-Za-z]{3}-\d{4})\s+(\d{2}-[A-Za-z]{3}-\d{4})",

            "Period To":
                None,  # handled together with Period From below
        }

        for key, pattern in patterns.items():

            if pattern is None:
                continue

            value = self._search(pattern)

            if value:
                fields[key] = value

        # PAN of Deductor, TAN of Deductor and PAN of Employee are printed
        # as three header labels followed - one or two lines later - by a
        # single value line containing all three IDs in that same order:
        #   AAATP4426B DELP05972B BFGPG1812J
        # Matching them together (rather than label-then-value) avoids the
        # header text itself getting picked up as the "value".
        ids_match = re.search(
            r"([A-Z]{5}\d{4}[A-Z])\s+([A-Z]{4}\d{5}[A-Z])\s+([A-Z]{5}\d{4}[A-Z])",
            self.text
        )
        if ids_match:
            fields["PAN of Deductor"] = ids_match.group(1)
            fields["TAN of Deductor"] = ids_match.group(2)
            fields["PAN of Employee"] = ids_match.group(3)

        # Period From / Period To: the "From ... To ..." dates appear as a
        # pair of dd-Mon-yyyy values close together in the text.
        period_match = re.search(
            r"(\d{2}-[A-Za-z]{3}-\d{4})\s+(\d{2}-[A-Za-z]{3}-\d{4})",
            self.text
        )
        if period_match:
            fields["Period From"] = period_match.group(1)
            fields["Period To"] = period_match.group(2)

        return fields

    # --------------------------------------------------
    # Salary Section
    # --------------------------------------------------

    def extract_salary(self):

        return {

            "salary17_1":
                self._number(
                    r"Salary as per provisions contained in section 17\(1\)\s+([\d,]+\.\d{2})"
                ),

            "perquisites17_2":
                self._number(
                    r"section 17\(2\)[\s\S]{0,80}?([\d,]+\.\d{2})"
                ),

            "profits17_3":
                self._number(
                    r"section 17\(3\)[\s\S]{0,80}?([\d,]+\.\d{2})"
                ),

            "standardDeduction":
                self._number(
                    r"Standard deduction under section 16\(ia\)\s+([\d,]+\.\d{2})"
                ),

            "incomeChargeableSalaries":
                self._number(
                    r'Income chargeable under the head "Salaries"\s*\[[^\]]*\]\s+([\d,]+\.\d{2})'
                )
        }

    # --------------------------------------------------
    # Tax Section
    # --------------------------------------------------

    def extract_taxes(self):

        return {

            "taxOnIncome":
                self._number(
                    r"Tax on total income\s+([\d,]+\.\d{2})"
                ),

            "healthEducationCess":
                self._number(
                    r"Health and education cess\s+([\d,]+\.\d{2})"
                ),

            "taxPayable":
                self._number(
                    r"Tax payable\s*\([^)]*\)\s+([\d,]+\.\d{2})"
                ),

            "netTaxPayable":
                self._number(
                    r"Net tax payable\s*\([^)]*\)\s+([\d,]+\.\d{2})"
                )
        }

    # --------------------------------------------------
    # Part A specific: quarter-wise TDS summary
    # --------------------------------------------------

    def extract_quarterly_summary(self):

        rows = []

        pattern = re.compile(
            r"(Q[1-4])\s+([A-Z0-9]+)\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})"
        )

        for match in pattern.finditer(self.text):
            rows.append({
                "quarter": match.group(1),
                "receiptNumber": match.group(2),
                "amountPaidCredited": float(match.group(3).replace(",", "")),
                "taxDeducted": float(match.group(4).replace(",", "")),
                "taxDeposited": float(match.group(5).replace(",", "")),
            })

        total_match = re.search(
            r"Total \(Rs\.\)\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})",
            self.text
        )

        total = {}
        if total_match:
            total = {
                "amountPaidCredited": float(total_match.group(1).replace(",", "")),
                "taxDeducted": float(total_match.group(2).replace(",", "")),
                "taxDeposited": float(total_match.group(3).replace(",", "")),
            }

        return {
            "quarters": rows,
            "total": total
        }

    # --------------------------------------------------
    # Structured JSON
    # --------------------------------------------------

    def extract_structured(self):

        dynamic = self.extract_dynamic_fields()

        structured = {

            "employee": {
                "name": dynamic.get("Employee Name", ""),
                "pan": dynamic.get("PAN of Employee", "")
            },

            "employer": {
                "name": dynamic.get("Employer Name", ""),
                "pan": dynamic.get("PAN of Deductor", ""),
                "tan": dynamic.get("TAN of Deductor", "")
            },

            "assessment": {
                "assessmentYear": dynamic.get("Assessment Year", ""),
                "periodFrom": dynamic.get("Period From", ""),
                "periodTo": dynamic.get("Period To", "")
            },

            "salary": self.extract_salary(),

            "taxes": self.extract_taxes(),

            "quarterlySummary": self.extract_quarterly_summary()
        }

        return structured

    # --------------------------------------------------
    # Confidence
    # --------------------------------------------------

    def confidence(self):

        score = 0

        checks = [
            r"Assessment Year",
            r"PAN of (?:the )?Employee",
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
            "documentType": self.detect_document_type(),
            "confidence": self.confidence(),
            "dynamicFields": self.extract_dynamic_fields(),
            "structuredData": self.extract_structured()
        }

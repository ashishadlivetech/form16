import re


PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
TAN_RE = re.compile(r"^[A-Z]{4}[0-9]{5}[A-Z]$")
AY_RE = re.compile(r"^\d{4}-\d{2}$")


class Form16Validator:

    def __init__(self, parsed_data: dict):
        self.data = parsed_data
        self.structured = parsed_data.get("structuredData", {})
        self.doc_type = parsed_data.get("documentType", "")

    def validate(self):

        warnings = []

        employer = self.structured.get("employer", {})
        employee = self.structured.get("employee", {})
        assessment = self.structured.get("assessment", {})
        salary = self.structured.get("salary", {})
        taxes = self.structured.get("taxes", {})
        quarterly = self.structured.get("quarterlySummary", {})

        if not employer.get("pan") or not PAN_RE.match(employer.get("pan", "")):
            warnings.append("Employer PAN invalid or missing.")

        if not employer.get("tan") or not TAN_RE.match(employer.get("tan", "")):
            warnings.append("Employer TAN invalid or missing.")

        if not employee.get("pan") or not PAN_RE.match(employee.get("pan", "")):
            warnings.append("Employee PAN invalid or missing.")

        if not assessment.get("assessmentYear") or not AY_RE.match(assessment.get("assessmentYear", "")):
            warnings.append("Assessment year invalid or missing.")

        if not employee.get("name"):
            warnings.append("Employee name not detected.")

        if not employer.get("name"):
            warnings.append("Employer name not detected.")

        # Salary / tax figures are only expected on Part B.
        if self.doc_type == "FORM16_PART_B":

            if not salary.get("salary17_1"):
                warnings.append("Gross salary not detected.")

            tax_payable = taxes.get("taxPayable", 0)
            net_tax_payable = taxes.get("netTaxPayable", 0)

            if tax_payable and net_tax_payable and tax_payable != net_tax_payable:
                # Difference is fine if reliefs (section 89) etc. were
                # subtracted; only flag if the gap looks like a parsing
                # error (e.g. wildly different orders of magnitude).
                if abs(tax_payable - net_tax_payable) > max(tax_payable, net_tax_payable) * 0.5:
                    warnings.append(
                        f"Tax mismatch. Tax payable {tax_payable}, "
                        f"net tax payable {net_tax_payable}."
                    )

        # Quarterly totals are only expected on Part A.
        if self.doc_type == "FORM16_PART_A":

            quarters = quarterly.get("quarters", [])
            total = quarterly.get("total", {})

            if not quarters:
                warnings.append("Quarterly TDS summary not detected.")
            else:
                computed_tax = round(sum(q["taxDeducted"] for q in quarters), 2)
                expected_tax = total.get("taxDeducted")

                if expected_tax is not None and computed_tax != expected_tax:
                    warnings.append(
                        f"Quarterly tax sum mismatch. Computed {computed_tax}, "
                        f"stated total {expected_tax}."
                    )

        confidence = self.data.get("confidence", 0)

        return {
            "isValid": len(warnings) == 0,
            "confidence": confidence,
            "warnings": warnings
        }

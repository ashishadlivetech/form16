import re


class Form16Validator:

    def __init__(self, data: dict):
        self.data = data

    # ---------------------------------------
    # PAN Validation
    # ---------------------------------------

    def validate_pan(self, pan):

        if not pan:
            return False

        pattern = r"^[A-Z]{5}[0-9]{4}[A-Z]$"

        return bool(re.match(pattern, pan))

    # ---------------------------------------
    # TAN Validation
    # ---------------------------------------

    def validate_tan(self, tan):

        if not tan:
            return False

        pattern = r"^[A-Z]{4}[0-9]{5}[A-Z]$"

        return bool(re.match(pattern, tan))

    # ---------------------------------------
    # AY Validation
    # ---------------------------------------

    def validate_assessment_year(self, ay):

        if not ay:
            return False

        pattern = r"^\d{4}-\d{2}$"

        return bool(re.match(pattern, ay))

    # ---------------------------------------
    # Salary Validation
    # ---------------------------------------

    def validate_salary(self):

        warnings = []

        salary = (
            self.data
            .get("structuredData", {})
            .get("salary", {})
        )

        gross_salary = salary.get("salary17_1", 0)

        taxable_salary = salary.get(
            "incomeChargeableSalaries",
            0
        )

        standard_deduction = salary.get(
            "standardDeduction",
            0
        )

        if gross_salary <= 0:
            warnings.append(
                "Gross salary not detected."
            )

        if taxable_salary < 0:
            warnings.append(
                "Taxable salary is invalid."
            )

        if standard_deduction > gross_salary:
            warnings.append(
                "Standard deduction exceeds salary."
            )

        return warnings

    # ---------------------------------------
    # Tax Validation
    # ---------------------------------------

    def validate_taxes(self):

        warnings = []

        taxes = (
            self.data
            .get("structuredData", {})
            .get("taxes", {})
        )

        tax = taxes.get("taxOnIncome", 0)

        cess = taxes.get(
            "healthEducationCess",
            0
        )

        net_tax = taxes.get(
            "netTaxPayable",
            0
        )

        expected_tax = tax + cess

        if (
            expected_tax > 0 and
            abs(expected_tax - net_tax) > 5
        ):
            warnings.append(
                f"Tax mismatch. Expected {expected_tax}, Found {net_tax}"
            )

        return warnings

    # ---------------------------------------
    # Employer Validation
    # ---------------------------------------

    def validate_employer(self):

        warnings = []

        employer = (
            self.data
            .get("structuredData", {})
            .get("employer", {})
        )

        if not employer.get("name"):
            warnings.append(
                "Employer name not found."
            )

        if not self.validate_pan(
            employer.get("pan", "")
        ):
            warnings.append(
                "Employer PAN invalid."
            )

        if not self.validate_tan(
            employer.get("tan", "")
        ):
            warnings.append(
                "Employer TAN invalid."
            )

        return warnings

    # ---------------------------------------
    # Employee Validation
    # ---------------------------------------

    def validate_employee(self):

        warnings = []

        employee = (
            self.data
            .get("structuredData", {})
            .get("employee", {})
        )

        if not employee.get("name"):
            warnings.append(
                "Employee name not found."
            )

        if not self.validate_pan(
            employee.get("pan", "")
        ):
            warnings.append(
                "Employee PAN invalid."
            )

        return warnings

    # ---------------------------------------
    # Assessment Validation
    # ---------------------------------------

    def validate_assessment(self):

        warnings = []

        assessment = (
            self.data
            .get("structuredData", {})
            .get("assessment", {})
        )

        ay = assessment.get(
            "assessmentYear",
            ""
        )

        if not self.validate_assessment_year(ay):
            warnings.append(
                "Assessment year invalid."
            )

        if not assessment.get("periodFrom"):
            warnings.append(
                "Employment start date missing."
            )

        if not assessment.get("periodTo"):
            warnings.append(
                "Employment end date missing."
            )

        return warnings

    # ---------------------------------------
    # Confidence Adjustment
    # ---------------------------------------

    def calculate_confidence(self):

        base = self.data.get(
            "confidence",
            0
        )

        warnings = self.get_all_warnings()

        deduction = len(warnings) * 5

        final_score = base - deduction

        if final_score < 0:
            final_score = 0

        return final_score

    # ---------------------------------------
    # All Warnings
    # ---------------------------------------

    def get_all_warnings(self):

        warnings = []

        warnings.extend(
            self.validate_employee()
        )

        warnings.extend(
            self.validate_employer()
        )

        warnings.extend(
            self.validate_assessment()
        )

        warnings.extend(
            self.validate_salary()
        )

        warnings.extend(
            self.validate_taxes()
        )

        return warnings

    # ---------------------------------------
    # Final Validation Result
    # ---------------------------------------

    def validate(self):

        warnings = self.get_all_warnings()

        return {

            "isValid":
                len(warnings) == 0,

            "confidence":
                self.calculate_confidence(),

            "warnings":
                warnings
        }
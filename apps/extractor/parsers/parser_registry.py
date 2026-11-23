from parsers.base_parser import BankParser
from parsers.generic_parser import GenericParser
from parsers.inspection import PDFError, inspect_pdf
from schemas import Extraction, Issue

# Add verified bank-specific profiles before the generic parser.
PARSERS: tuple[BankParser, ...] = (GenericParser(),)


def extract(data: bytes, maximum_pages: int, expected_last4: str | None) -> Extraction:
    try:
        document = inspect_pdf(data, maximum_pages)
    except PDFError as error:
        if error.code != "NO_TEXT_LAYER":
            raise
        from parsers.legacy_statement import extract_legacy_statement

        result = extract_legacy_statement(data, maximum_pages, raise_if_recognized=True)
        if result is None:
            raise
    else:
        parser = next(profile for profile in PARSERS if profile.supports(document))
        result = parser.extract(document)
    if any(issue.code == "MULTIPLE_ACCOUNTS" for issue in result.issues) or (
        expected_last4 and result.account_last4 and expected_last4 != result.account_last4
    ):
        result.status = "UNSUPPORTED"
        result.transactions = []
        result.issues.append(Issue(code="ACCOUNT_MISMATCH",
                                   message="The statement account does not match the selected bank account. Select the correct account and upload again."))
    return result

from base64 import b64decode
from hashlib import sha256
from io import BytesIO

from openpyxl import load_workbook

from demo import convert, sample_pdf
from exporters import approved_register
from parsers.parser_registry import extract


def test_reviewed_accounting_fields_are_bound_to_exact_statement(monkeypatch):
    pdf = sample_pdf()
    statement = extract(pdf, 40, None)
    check = statement.transactions[1]
    fingerprint = approved_register.transaction_fingerprint(check)
    source = {
        sha256(pdf).hexdigest(): {
            fingerprint: {"name": None, "split": "Reviewed account", "text_split": True, "height": 22.8},
        },
    }
    monkeypatch.setattr(approved_register, "reviewed_statements", lambda: source)

    assert approved_register.reviewed_register(pdf + b"\n") == {}
    result = convert(pdf)
    rows = result["register"]["rows"]
    assert rows[1]["split"] == "Reviewed account"
    assert rows[1]["name"] is None
    assert rows[1]["date"] is None
    assert rows[1]["bank_clear_date"] == "07/06/2026"
    assert rows[2]["split"] is None
    workbook = load_workbook(BytesIO(b64decode(result["workbook"])))
    assert workbook.active["S4"].font.sz == 8
    assert workbook.active["S4"].number_format == "@"
    assert workbook.active.row_dimensions[4].height == 22.8

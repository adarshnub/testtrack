from base64 import b64decode
from datetime import datetime
from io import BytesIO

from fastapi.testclient import TestClient
from openpyxl import load_workbook
import pymupdf
import pytest

from demo import convert, sample_pdf
from demo_local import app
from parsers.inspection import PDFError


def test_sample_reconciles_and_exports_review_workbook():
    result = convert(sample_pdf())
    reconciliation = result["reconciliation"]
    assert (reconciliation["status"], reconciliation["count"], reconciliation["difference"]) == ("PASSED", 6, "0.00")
    assert reconciliation["expected"] == "6129.57"
    assert result["rows"][1]["action"] == "CHECK_DETAILS_REQUIRED"
    assert result["rows"][4]["action"] == "REVIEW"
    book = load_workbook(BytesIO(b64decode(result["workbook"])))
    assert len(book.sheetnames) == 11
    assert book.active.title == "Statement Register"
    assert book["BANK_TRANSACTIONS"]["G2"].value == 1200
    assert book["BANK_TRANSACTIONS"]["C2"].value == datetime(2026, 7, 3)
    assert book["CHECK_REGISTER"]["D2"].value == datetime(2026, 7, 6)
    assert book["CHECK_REGISTER"]["E2"].value is None
    assert book["NEEDS_MAPPING"]["L2"].value is None
    assert book["NEEDS_MAPPING"].max_row == 7
    assert book["IIF_STAGING"].max_row == 1


def test_mismatch_is_visible():
    with pymupdf.open(stream=sample_pdf(), filetype="pdf") as pdf:
        page = pdf[0]
        page.add_redact_annot(page.search_for("$6,129.57")[0], fill=False)
        page.apply_redactions()
        page.insert_text((124, 220), "$6,129.58", fontsize=11)
        result = convert(pdf.tobytes())
    assert result["reconciliation"]["status"] == "FAILED"
    assert result["reconciliation"]["difference"] == "-0.01"


def test_image_only_and_invalid_pdfs_fail_without_workbook():
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.draw_rect(pymupdf.Rect(50, 50, 300, 200), color=(0, 0, 0))
        with pytest.raises(PDFError, match="No searchable text"):
            convert(pdf.tobytes())
    with pytest.raises(PDFError, match="valid PDF header"):
        convert(b"not a pdf")


def test_flattened_vector_pdf_points_to_searchable_original():
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        glyph = page.new_shape()
        glyph.draw_polyline([(50, 70), (56, 55), (62, 70), (59, 70), (56, 61), (53, 70), (50, 70)])
        glyph.finish(color=None, fill=(0, 0, 0))
        glyph.commit()
        with pytest.raises(PDFError, match="original bank statement with selectable text") as error:
            convert(pdf.tobytes())
        assert error.value.code == "NO_TEXT_LAYER"


def test_unsupported_text_layout():
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((50, 70), "We did not find a transaction table here.")
        with pytest.raises(PDFError, match="no supported transaction table"):
            convert(pdf.tobytes())


def test_multipage_sections_and_wrapped_description():
    with pymupdf.open() as pdf:
        first = pdf.new_page()
        for y, line in (
            (50, "Statement period 12/01/2025 - 01/03/2026"), (70, "Beginning balance 95.00"),
            (90, "Ending balance 250.00"), (120, "ACH Credits"),
        ):
            first.insert_text((40, y), line)
        for x, y, value in (
            (40, 150, "Date"), (120, 150, "Description"), (400, 150, "Amount"),
            (40, 170, "12/30"), (120, 170, "ACH Credit"), (400, 170, "200.00"),
            (120, 190, "Wrapped invoice description"), (40, 220, "Total ACH credits 200.00"),
        ):
            first.insert_text((x, y), value)
        second = pdf.new_page()
        for x, y, value in (
            (40, 50, "Checks paid"),
            (40, 80, "Date"), (120, 80, "Check number"), (400, 80, "Amount"),
            (40, 100, "01/02"), (120, 100, "0123"), (400, 100, "(45.00)"),
            (40, 140, "Total checks paid 45.00"),
        ):
            second.insert_text((x, y), value)
        result = convert(pdf.tobytes())
    assert result["reconciliation"]["status"] == "PASSED"
    assert result["rows"][0]["original_description"] == "ACH Credit\nWrapped invoice description"
    assert result["rows"][1]["statement_date"] == "2026-01-02"
    assert result["rows"][1]["source_page"] == 2
    assert result["rows"][1]["check_number"] == "0123"


def test_public_visitor_converts_statement_and_receives_workbook():
    client = TestClient(app)
    page = client.get("/")
    assert "Statement Studio" in page.text
    sample = client.get("/sample.pdf")
    assert sample.content.startswith(b"%PDF")
    response = client.post("/api/convert", content=sample.content, headers={"Content-Type": "application/pdf"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    result = response.json()
    assert result["reconciliation"]["status"] == "PASSED"
    assert b64decode(result["workbook"]).startswith(b"PK")

from datetime import date
from decimal import Decimal
import os
from pathlib import Path

import pymupdf
import pytest

from parsers.inspection import PDFError
from parsers.legacy_statement import PROFILE_ID, SECTIONS, eligible, extract_legacy_statement, legacy_data_ready


def synthetic_outline_statement(*, closing: str = "520.00", shifted_sections: bool = False) -> bytes:
    """Draw a fake six-page statement and convert every visible character to a vector path."""
    with pymupdf.open() as source, pymupdf.open() as outlined:
        for _ in range(6):
            source.new_page(width=595.28, height=841.89)

        def write(page: int, y: float, *fields: tuple[float, str], bold: bool = False):
            font = "hebo" if bold else "helv"
            for x, value in fields:
                source[page - 1].insert_text((x, y + 3), value, fontname=font, fontsize=9)

        write(1, 366, (36, "Account Summary"), bold=True)
        write(1, 389, (36, "02/01/2025"), (112, "Beginning Balance"), (275, "400.00"))
        write(1, 400, (73, "Items 3"), (112, "Total credits"), (275, "200.00"))
        write(1, 411, (73, "Items 6"), (112, "Total debits"), (275, "80.00"))
        write(1, 422, (36, "02/28/2025"), (112, "Ending Balance"), (275, closing))
        write(2, 100, (36, "Synthetic notice page outside the transaction regions"), bold=True)
        sections = (
            ([("02/03", "Branch Deposit", "100.00")], "100.00"),
            ([("02/05", "Payroll Credit", "35.00"), ("02/07", "Card Refund", "65.00")], "100.00"),
            ([("02/10", "Vendor Payment", "20.00"), ("02/13", "Online Service", "15.00")], "35.00"),
            ([("02/17", "Debit Adjustment", "5.00"), ("02/20", "Service Fee", "10.00")], "15.00"),
        )
        layout = (
            (((1, 457),), 705),
            (((3, 193), (4, 147)), 615 if shifted_sections else 450),
            (((4, 646 if shifted_sections else 481), (5, 193)), 579 if shifted_sections else 724),
            (((5, 610 if shifted_sections else 755), (6, 147)), 223 if shifted_sections else 252),
        )
        for section, (rows, section_total), (regions, total_y) in zip(SECTIONS[:-1], sections, layout, strict=True):
            for index, ((page, header_y), (posted, description, amount)) in enumerate(zip(regions, rows, strict=True)):
                title = f"{section.title} (continued)" if index else section.title
                write(page, header_y - 10, (36, title), bold=True)
                write(page, header_y, (36, "Date"), (95, "Description"), (518, "Amount"), bold=True)
                write(page, header_y + 12, (36, posted), (95, description), (518, amount))
            write(regions[-1][0], total_y, (95, "Section total"), (408, f"Count {len(rows)}"), (518, section_total))

        check_header = 255 if shifted_sections else 285
        write(6, check_header - 11, (36, "Checks Cleared"), bold=True)
        write(6, check_header, (87, "Check"), (135, "Date"), (237, "Amount"),
              (367, "Check"), (415, "Date"), (522, "Amount"), bold=True)
        write(6, check_header + 19, (87, "701"), (135, "02/"), (154, "21"), (237, "12.00"),
              (367, "702"), (415, "02/24"), (522, "18.00"))
        write(6, 389 if shifted_sections else 396, (36, "Total checks"), (408, "Count 2"), (518, "30.00"))
        write(6, 440, (36, "Out-of-region content belongs outside this check register"))

        for page in source:
            with pymupdf.open(stream=page.get_svg_image(text_as_path=True).encode(), filetype="svg") as svg:
                with pymupdf.open(stream=svg.convert_to_pdf(), filetype="pdf") as vector_page:
                    outlined.insert_pdf(vector_page)
        return outlined.tobytes(garbage=4, deflate=True)


def test_synthetic_statement_matches_geometry_and_has_no_text_layer():
    with pymupdf.open(stream=synthetic_outline_statement(), filetype="pdf") as statement:
        assert eligible(statement)
        assert all(not page.get_text() for page in statement)


@pytest.fixture
def legacy_runtime():
    pytest.importorskip("tesserocr")
    directory = Path(os.getenv("BANKLEDGER_TESSDATA_DIR", Path(__file__).resolve().parents[1] / "tessdata"))
    if not legacy_data_ready(directory):
        pytest.skip("The optional legacy English recognition data is unavailable.")


@pytest.mark.parametrize("shifted_sections", [False, True])
def test_synthetic_legacy_statement_matches_printed_controls(legacy_runtime, shifted_sections):
    extracted = extract_legacy_statement(synthetic_outline_statement(shifted_sections=shifted_sections), maximum_pages=6)
    assert extracted is not None
    assert extracted.parser == PROFILE_ID
    assert extracted.summary.count == len(extracted.transactions) == 9
    assert extracted.summary.opening == Decimal("400.00")
    assert extracted.summary.closing == Decimal("520.00")
    assert extracted.summary.credits == Decimal("200.00")
    assert extracted.summary.debits == Decimal("80.00")
    assert all(row.source_bounds for row in extracted.transactions)
    checks = [row for row in extracted.transactions if row.category == "CLEARED_CHECK"]
    assert [(row.check_number, row.source_page) for row in checks] == [("701", 6), ("702", 6)]
    assert [row.statement_date for row in checks] == [date(2025, 2, 21), date(2025, 2, 24)]


def test_synthetic_legacy_statement_rejects_inconsistent_printed_closing(legacy_runtime):
    invalid = synthetic_outline_statement(closing="520.01")
    assert extract_legacy_statement(invalid, maximum_pages=6) is None
    with pytest.raises(PDFError) as failure:
        extract_legacy_statement(invalid, maximum_pages=6, raise_if_recognized=True)
    assert failure.value.code == "OUTLINED_STATEMENT_UNVERIFIED"

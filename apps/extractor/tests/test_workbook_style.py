from base64 import b64decode
from datetime import datetime
from decimal import Decimal
from io import BytesIO
from xml.etree import ElementTree
from zipfile import ZipFile

from openpyxl import Workbook, load_workbook

from demo import convert, sample_pdf
from exporters.excel import BOOK11_MONEY_FORMAT, register_cell


def test_debit_is_written_as_exact_decimal_numeric_xml():
    workbook = Workbook()
    register_cell(workbook.active, 24, 21, Decimal("93.60"), money_format=BOOK11_MONEY_FORMAT)
    output = BytesIO()
    workbook.save(output)
    with ZipFile(output) as archive:
        sheet = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    cell = sheet.find(".//{*}c[@r='U24']")
    assert cell is not None and cell.get("t") == "n"
    assert cell.findtext("{*}v") == "93.60"
    loaded = load_workbook(output).active["U24"]
    assert loaded.data_type == "n" and loaded.value == 93.6
    assert loaded.number_format == BOOK11_MONEY_FORMAT


def test_export_opens_on_reference_style_register():
    result = convert(sample_pdf())
    workbook = load_workbook(BytesIO(b64decode(result["workbook"])))
    register = workbook.active

    assert register.title == "Statement Register"
    assert register.freeze_panes == "C2"
    assert register.max_column == 23
    assert [register[f"{column}1"].value for column in "EGIKMOQSUW"] == [
        "Type", "Date", "Num", "Adj", "Name", "Memo", "Clr", "Split", "Debit", "Credit",
    ]
    assert register.auto_filter.ref == "E1:Y9"
    assert register.sheet_view.showGridLines is False
    assert register.sheet_format.defaultRowHeight == 14.4
    assert register.column_dimensions["A"].width == 2.21875
    assert register.column_dimensions["O"].width == 35
    assert register["E1"].font.bold
    assert register["E1"].border.bottom.style == "medium"
    assert register["B2"].value == "Beginning Balance"
    assert register["E3"].value == "Deposit"
    assert register["G3"].value == datetime(2026, 7, 3)
    assert register["G3"].number_format == "mm/dd/yyyy"
    assert register["O3"].value == "Cash deposit"
    assert register["O3"].alignment.wrap_text
    assert register["S3"].value is None
    assert register["U3"].value == 1200
    assert register["U3"].number_format == "#,##0.00_);(#,##0.00)"
    assert register["W3"].value is None
    assert register["B9"].value == "Total"
    assert register["U9"].value == 3630
    assert register["W9"].value == 1780.93
    assert {"BANK_TRANSACTIONS", "TRANSACTION_REVIEW", "RECONCILIATION", "EXCEPTIONS"}.issubset(workbook.sheetnames)
    reconciliation = {row[0]: row[1] for row in workbook["RECONCILIATION"].iter_rows(min_row=2, values_only=True)}
    assert reconciliation["Opening balance"] == 4280.50
    assert reconciliation["Calculated ending balance"] == 6129.57
    review = workbook["TRANSACTION_REVIEW"]
    assert (review["U1"].value, review["V1"].value) == ("Register running balance", "Balance basis")
    assert (review["U2"].value, review["V2"].value) == (5480.50, "Printed")


def test_register_check_separates_clearing_and_accounting_dates():
    result = convert(sample_pdf())
    workbook = load_workbook(BytesIO(b64decode(result["workbook"])))
    register = workbook.active

    assert register["E4"].value == "Check"
    assert register["I4"].value == "1394"
    assert register["G4"].value is None
    assert workbook["CHECK_REGISTER"]["D2"].value == datetime(2026, 7, 6)
    assert workbook["BANK_TRANSACTIONS"]["B3"].value == 1
    assert workbook["TRANSACTION_REVIEW"]["B3"].value == "CHECK_DETAILS_REQUIRED"
    assert register["U4"].value is None
    assert register["W4"].value == 620.99


def test_web_register_matches_each_exported_cell():
    result = convert(sample_pdf())
    workbook = load_workbook(BytesIO(b64decode(result["workbook"])))
    sheet = workbook.active
    register = result["register"]
    columns = {
        "type": "E", "date": "G", "num": "I", "adj": "K", "name": "M", "memo": "O", "clr": "Q",
        "split": "S", "debit": "U", "credit": "W",
    }

    assert register["opening"] == result["reconciliation"]["opening"]
    assert len(register["rows"]) == 6
    for index, preview_row in enumerate(register["rows"], start=3):
        expected = {}
        for key, column in columns.items():
            value = sheet[f"{column}{index}"].value
            if isinstance(value, datetime):
                value = value.strftime("%m/%d/%Y")
            elif key in ("debit", "credit") and value is not None:
                value = f"{value:.2f}"
            expected[key] = value
        assert {key: preview_row[key] for key in columns} == expected
    assert register["totals"] == {
        "debit": f'{sheet["U9"].value:.2f}',
        "credit": f'{sheet["W9"].value:.2f}',
        "balance": result["reconciliation"]["calculated"],
    }

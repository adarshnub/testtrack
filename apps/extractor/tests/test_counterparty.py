from base64 import b64decode
from decimal import Decimal
from io import BytesIO

import pymupdf
from openpyxl import load_workbook
import pytest

from demo import convert, sample_pdf
from exporters import company_split, counterparty


def georgia_tax_pdf():
    tax_memo = "ACH Payment GEORGIA ITS TAX GA TX PYMT"
    outgoing = [(tax_memo, amount) for amount in ("10.18", "20.00", "300.00", "300.01", "556.13")]
    outgoing.append(("ACH Payment Kestrel Equipment", "11.00"))
    debits = sum(Decimal(amount) for _, amount in outgoing)
    closing = Decimal("2000.00") + Decimal("13.00") - debits
    with pymupdf.open() as doc:
        page = doc.new_page()
        for y, label in (
            (55, "Demo Community Bank"), (100, "Statement period: 07/01/2026 - 07/31/2026"),
            (150, "Beginning balance $2,000.00"), (170, "Total deposits (1) $13.00"),
            (190, f"Total withdrawals (6) ${debits:,.2f}"), (210, f"Ending balance ${closing:,.2f}"),
            (230, "Transaction count: 7"), (250, "Account activity"),
        ):
            page.insert_text((45, y), label, fontsize=11)
        for x, label in ((45, "Date"), (115, "Description"), (385, "Debit"), (470, "Credit")):
            page.insert_text((x, 275), label, fontsize=10)
        for index, (memo, amount) in enumerate(outgoing + [("ACH CREDIT Georgia ITS Tax", "13.00")]):
            y = 300 + index * 20
            for x, text in ((45, "07/03"), (115, memo), (385 if index < len(outgoing) else 470, amount)):
                page.insert_text((x, y), text, fontsize=10)
        return doc.tobytes()


def test_georgia_its_tax_split_uses_only_its_credit_amount():
    result = convert(georgia_tax_pdf())
    rows = result["register"]["rows"]
    expected = [
        "Wireless Payments", "Business License and Permits", "Business License and Permits",
        "Sales Tax Payable", "Sales Tax Payable", None, None,
    ]
    assert result["reconciliation"]["status"] == "PASSED"
    assert [row["split"] for row in rows] == expected
    assert rows[-1]["name"] == "Georgia ITS Tax" and rows[-1]["credit"] is None
    workbook = load_workbook(BytesIO(b64decode(result["workbook"])))
    assert [workbook.active[f"S{row}"].value for row in range(3, 10)] == expected


@pytest.fixture
def known_names(monkeypatch):
    entries = {
        "Blue Harbor Supply": "Office Supplies",
        "Alder Office Services": "Professional Fees",
        "Deposit": "Cash Deposits",
    }
    matches = {company_split.company_key(name): (name,) for name in entries}
    splits = tuple((company_split.company_key(name), split) for name, split in entries.items())
    monkeypatch.setattr(counterparty, "company_names", lambda: matches)
    monkeypatch.setattr(company_split, "saved_company_splits", lambda: splits)


def test_known_companies_use_reference_spelling_and_split(known_names):
    resolve = counterparty.resolve_counterparty
    assert resolve("ACH CREDIT BLUE-HARBOR, SUPPLY CCD REF: 123456") == ("Blue Harbor Supply", "Office Supplies")
    assert resolve("POS alder office services #3704") == ("Alder Office Services", "Professional Fees")
    assert resolve("Deposit") == ("Deposit", "Cash Deposits")


def test_printed_counterparties_do_not_require_a_match_in_the_catalog(known_names):
    resolve = counterparty.resolve_counterparty
    assert resolve("ACH PAYMENT Kestrel Equipment Co. CCD TRACE: 1234567") == ("Kestrel Equipment Co.", None)
    assert resolve("POS RADIAL STATIONERY #5739 HOUSTON TX") == ("RADIAL STATIONERY", None)
    assert resolve("Domestic wire ORIG CO NAME: Bright Circuit LLC REF: 889324") == ("Bright Circuit LLC", None)


def test_bare_checks_internal_transfers_and_ambiguous_payees_stay_blank(known_names):
    resolve = counterparty.resolve_counterparty
    assert resolve("Check 1394", physical_check=True) == (None, None)
    assert resolve("Check 1394 re Blue Harbor Supply", physical_check=True) == (None, None)
    assert resolve("Check 1394 Payee: BLUE HARBOR SUPPLY", physical_check=True) == ("Blue Harbor Supply", "Office Supplies")
    assert resolve("IB TRANSFER DEPOSIT FROM FREE BUSINESS CHECKING XXXX9876", transfer=True) == (None, None)
    assert resolve("PAYEE: Blue Harbor Supply | BENEFICIARY: Alder Office Services") == (None, None)
    assert resolve("ACH PAYMENT PPD REF: 123456") == (None, None)
    assert resolve("ACH DEPOSITION 012345") == (None, None)


def test_reviewed_narration_aliases_follow_payment_family_and_keep_ambiguous_split_blank(monkeypatch):
    monkeypatch.setattr(counterparty, "book11_rules", lambda: {
        "parties": {
            "ach-credit:kestrel equipment llc": {"name": "Kestrel Equipment", "split": "Vendor Credits"},
            "ach-debit:bright circuit llc": {"name": "Bright Circuit", "split": None},
            "ach-credit:returned service": {"name": None, "split": "Returned Funds"},
        },
        "narrations": {"overdraft paid fee": {"name": "Bank charge", "split": "Bank Fees"}},
        "transfers": {},
    })
    resolve = counterparty.resolve_counterparty
    assert resolve("ACH CREDIT Kestrel Equipment LLC CCD TRACE: 1234567") == ("Kestrel Equipment", "Vendor Credits")
    assert resolve("ACH PAYMENT Kestrel Equipment LLC CCD") == ("Kestrel Equipment LLC", None)
    assert resolve("ACH PAYMENT Bright Circuit LLC CCD") == ("Bright Circuit", None)
    assert resolve("ACH CREDIT Returned Service CCD") == (None, "Returned Funds")
    assert resolve("Overdraft paid fee") == ("Bank charge", "Bank Fees")


def test_reviewed_transfer_uses_only_matching_masked_counterpart(monkeypatch):
    incoming = "IB TRANSFER DEPOSIT IB XFR FRM FREE BUS C XXX9998"
    key = counterparty.transfer_key(incoming)
    assert key
    monkeypatch.setattr(counterparty, "book11_rules", lambda: {
        "parties": {}, "narrations": {}, "transfers": {key: "Operating Account"},
    })
    resolve = counterparty.resolve_counterparty
    assert resolve(incoming, transfer=True) == (None, "Operating Account")
    assert resolve("IB TRANSFER W/D IB XFR TO FREE BUS C XXXX 9998", transfer=True) == (None, "Operating Account")
    assert resolve("IB TRANSFER DEPOSIT IB XFR FRM FREE BUS C XXX9997", transfer=True) == (None, None)
    assert resolve("IB TRANSFER DEPOSIT IB XFR FRM SAVE C XXX9998", transfer=True) == (None, None)
    assert resolve("Check 9998", physical_check=True) == (None, None)


def test_converted_workbook_shows_name_alongside_existing_bank_memo(known_names):
    result = convert(sample_pdf())
    rows = result["register"]["rows"]
    assert rows[1]["name"] is None
    assert rows[2]["name"] == "Blue Harbor Supply"
    assert rows[2]["memo"] == "ACH CREDIT Blue Harbor Supply"
    assert rows[2]["split"] == "Office Supplies"
    assert rows[5]["name"] == "Birch Street Market"
    assert rows[5]["split"] is None
    with BytesIO(b64decode(result["workbook"])) as stream:
        workbook = load_workbook(stream)
        assert workbook.active["M5"].value == "Blue Harbor Supply"
        assert workbook.active["M8"].value == "Birch Street Market"
        assert workbook.active["S8"].value is None

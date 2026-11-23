from base64 import b64decode
from io import BytesIO

from openpyxl import load_workbook
import pytest

from demo import convert, sample_pdf
from exporters import company_split


@pytest.fixture
def sample_split_rules(monkeypatch):
    pairs = {
        "Blue Harbor Supply": "Office Supplies",
        "Alder Office Services": "Professional Fees",
        "Elm Plaza": "Office Supplies",
        "Deposit": "Cash Deposits",
        "Withdrawal": "Cash Withdrawals",
    }
    saved = tuple((company_split.company_key(name), split) for name, split in pairs.items())
    monkeypatch.setattr(company_split, "saved_company_splits", lambda: saved)


def test_company_matching_uses_complete_names_and_exact_only_bank_labels(sample_split_rules):
    find_split = company_split.find_company_split
    assert find_split("ACH CREDIT blue-harbor  SUPPLY *883") == "Office Supplies"
    assert find_split("ACH CREDIT Blue Harbor Suppliers") is None
    assert find_split("Deposit") == "Cash Deposits"
    assert find_split("IB Transfer Deposit From Another Account") is None
    assert find_split("Withdrawal - ACH Alder OFFICE services") == "Professional Fees"
    assert find_split("Unknown company") is None


def test_multiple_company_matches_only_assign_a_shared_split(sample_split_rules):
    find_split = company_split.find_company_split
    assert find_split("BLUE HARBOR SUPPLY FOR ELM PLAZA") == "Office Supplies"
    assert find_split("BLUE HARBOR SUPPLY FOR ALDER OFFICE SERVICES") is None


def test_matched_and_blank_splits_are_shared_by_preview_and_excel(sample_split_rules):
    result = convert(sample_pdf())
    splits = [row["split"] for row in result["register"]["rows"]]
    assert splits == [None, None, "Office Supplies", "Professional Fees", None, None]

    workbook = load_workbook(BytesIO(b64decode(result["workbook"])))
    assert [workbook.active[f"S{row}"].value for row in range(3, 9)] == splits
    assert workbook["NEEDS_MAPPING"].max_row == 5
    assert result["register"]["rows"][1]["date"] is None

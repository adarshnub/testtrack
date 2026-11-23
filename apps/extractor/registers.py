import csv
from datetime import date, datetime
from io import BytesIO, StringIO
import re
from zipfile import BadZipFile, ZipFile

from openpyxl import load_workbook

from normalization import parse_date, parse_money

MAX_ROWS = 25_000
MAX_COLS = 80


def table_rows(data: bytes, file_type: str):
    if file_type == "csv":
        try:
            content = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            content = data.decode("cp1252")
        if "\x00" in content:
            raise ValueError("The CSV contains binary content.")
        reader = csv.reader(StringIO(content), strict=True)
        for index, row in enumerate(reader):
            if index >= MAX_ROWS or len(row) > MAX_COLS:
                raise ValueError("The spreadsheet exceeds the row or column limit.")
            yield row
        return

    try:
        with ZipFile(BytesIO(data)) as archive:
            parts = archive.infolist()
            if len(parts) > 300 or sum(part.file_size for part in parts) > 45 * 1024 * 1024:
                raise ValueError("The Excel file exceeds the decompressed size limit.")
            if any(part.flag_bits & 1 for part in parts):
                raise ValueError("Upload an unlocked Excel workbook.")
        workbook = load_workbook(BytesIO(data), read_only=True, data_only=False, keep_links=False)
    except (BadZipFile, OSError, KeyError, LookupError):
        raise ValueError("Upload a valid .xlsx workbook or .csv file.") from None
    try:
        sheet = workbook.worksheets[0]
        if (sheet.max_column or 0) > MAX_COLS or (sheet.max_row or 0) > MAX_ROWS:
            raise ValueError("The first Excel sheet exceeds the row or column limit.")
        for index, row in enumerate(sheet.iter_rows(max_col=MAX_COLS)):
            if index >= MAX_ROWS:
                raise ValueError("The first Excel sheet exceeds the row limit.")
            if any(cell.data_type in ("f", "e") for cell in row):
                raise ValueError(f"Excel row {index + 1} contains a formula or cell error. Export values before uploading.")
            yield [cell.value for cell in row]
    finally:
        workbook.close()


def heading(value):
    return re.sub(r"[^a-z0-9]", "", string(value).lower())


def string(value):
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    return str(value).strip()


def find_columns(row, aliases):
    keyed = [heading(cell) for cell in row]
    return {key: keyed.index(label) for key, names in aliases.items()
            if (label := next((name for name in names if name in keyed), None))}


LEDGER_COLUMNS = {
    "type": ("type", "transactiontype"), "date": ("date", "transactiondate"),
    "number": ("num", "number", "checknumber", "docnum", "refno"),
    "name": ("name", "payee", "vendor"), "memo": ("memo", "description"),
    "cleared": ("clr", "cleared", "reconciled"), "split": ("split", "splitaccount", "account"),
    "debit": ("debit", "debits"), "credit": ("credit", "credits"),
    "deposit": ("deposit", "deposits", "moneyin"), "payment": ("payment", "payments", "withdrawal", "moneyout"),
    "balance": ("balance", "runningbalance"),
}


def parse_register(data: bytes, file_type: str, debit_side: str):
    columns = {}
    records = []
    row_errors = []
    for index, row in enumerate(table_rows(data, file_type), 1):
        if not any(string(cell) for cell in row):
            continue
        possible = find_columns(row, LEDGER_COLUMNS)
        has_amount = {"debit", "credit"}.issubset(possible) or {"deposit", "payment"}.issubset(possible)
        if "date" in possible and has_amount and "type" in possible:
            columns = possible
            continue
        if not columns:
            if index > 30:
                break
            continue
        get = {key: string(row[position]) if position < len(row) else "" for key, position in columns.items()}
        if not get["date"] and (not get["type"] or re.match(r"^(total|ending|beginning|opening|closing|balance)", get["type"], re.I)):
            if all(get.get(key, "") in ("", "-") for key in ("payment", "deposit", "debit", "credit")) or "total" in " ".join(get.values()).lower():
                continue
        dated = parse_date(get["date"])
        incoming, outgoing = (("deposit", "payment") if "deposit" in get and "payment" in get else
                              ("debit", "credit") if debit_side == "DEBIT_IN" else ("credit", "debit"))
        amounts = {}
        for key in (incoming, outgoing, "balance"):
            raw = get.get(key, "")
            amounts[key] = parse_money(raw, allow_integer=True) if raw not in ("", "-", "—") else None
        amounts_present = [(key, amount) for key, amount in amounts.items() if key != "balance" and amount]
        if not dated or not get["type"] or len(amounts_present) != 1 or any(
            get.get(k, "") not in ("", "-", "—") and amounts[k] is None for k in amounts
        ):
            row_errors.append(index)
            continue
        key, amount = amounts_present[0]
        mark = get.get("cleared")
        mark = mark.lower() if mark is not None else None
        cleared = True if mark in ("y", "yes", "true", "c", "r", "x", "✓", "cleared", "reconciled", "*") else (
            False if mark in ("", "n", "no", "false", "uncleared", "outstanding") else None)
        limits = {"type": 80, "number": 120, "name": 240, "memo": 500, "split": 240}
        if any(len(get.get(field, "")) > limit for field, limit in limits.items()):
            row_errors.append(index)
            continue
        records.append({
            "position": index, "type": get["type"], "date": dated.isoformat(),
            "number": get.get("number") or None, "name": get.get("name", ""), "memo": get.get("memo", ""),
            "cleared": cleared, "split": get.get("split", ""), "amount": f"{abs(amount):.2f}",
            "direction": "IN" if key == incoming else "OUT",
            "balance": f"{amounts['balance']:.2f}" if amounts["balance"] is not None else None,
            "original": get,
        })
    if row_errors:
        raise ValueError("The register includes invalid date, amount, or field values on spreadsheet rows " +
                         ", ".join(map(str, row_errors[:12])) + ". Fix those rows before uploading.")
    if not records:
        raise ValueError("No register entries were found. The first sheet needs Type, Date, and paired Payment/Deposit or Debit/Credit columns.")
    return records


def parse_chart(data: bytes, file_type: str):
    aliases = {
        "name": ("fullname", "fullaccountname", "accountname", "nameofaccount", "account", "name", "accnt"),
        "type": ("accounttype", "type", "accnttype"),
    }
    columns = {}
    records: dict[str, dict] = {}
    for index, row in enumerate(table_rows(data, file_type), 1):
        if not any(string(cell) for cell in row):
            continue
        if not columns:
            columns = find_columns(row, aliases)
            if "name" not in columns:
                columns = {}
                if index > 30:
                    break
            continue
        if columns["name"] >= len(row) or not (name := string(row[columns["name"]])):
            continue
        col = columns.get("type")
        account_type = string(row[col]) if col is not None and col < len(row) else None
        if len(name) > 240 or len(account_type or "") > 120 or any(ord(char) < 32 for char in name):
            raise ValueError(f"The account name or type on spreadsheet row {index} is invalid.")
        entry = {"name": name, "type": account_type or None}
        if name in records and records[name] != entry:
            raise ValueError(f"The same full account name has conflicting types on row {index}.")
        records[name] = entry
    if not records:
        raise ValueError("No accounts were found. Add an Account or Full Account Name column to the first sheet.")
    return list(records.values())

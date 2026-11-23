import csv
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from io import StringIO
import re

from schemas import ExportInput, ExportRow, Split

# Columns documented in Intuit's IIF kit for check, deposit and transfer.
TRNS = ("!TRNS", "TRNSID", "TRNSTYPE", "DATE", "ACCNT", "NAME", "CLASS", "AMOUNT", "DOCNUM", "MEMO", "CLEAR", "TOPRINT")
SPL = ("!SPL", "SPLID", "TRNSTYPE", "DATE", "ACCNT", "NAME", "CLASS", "AMOUNT", "DOCNUM", "MEMO", "CLEAR")
HEADERS = (TRNS, SPL, ("!ENDTRNS",))
MONEY_PATTERN = re.compile(r"-?\d+\.\d{2}")


@dataclass(frozen=True)
class Candidate:
    row: ExportRow
    splits: tuple[Split, ...]
    reasons: tuple[str, ...]


def safe_field(text: str) -> bool:
    return all(32 <= ord(character) <= 126 for character in text)


def evaluate(payload: ExportInput) -> list[Candidate]:
    chart = set(payload.chart) if payload.chart is not None else None
    counterparts = set(payload.counterpart_accounts)
    results = []
    for row in payload.rows:
        reasons: list[str] = []
        if row.action != "IMPORT":
            reasons.append({
                "MATCH_EXISTING": "Already recorded in the available register or import history.",
                "SKIP": "Skipped during review.",
                "REVIEW": "Requires review.",
                "CHECK_DETAILS_REQUIRED": "Check written date or payee is still required.",
            }[row.action])
        if row.exported:
            reasons.append("Already included in a saved IIF batch.")
        if row.duplicate_blocked and not row.duplicate_override:
            reasons.append("Potential duplicate. A documented review is required.")
        if row.qb_date is None or row.statement_date is None or row.amount is None or row.amount <= 0 or row.direction is None:
            reasons.append("A valid bank date, QuickBooks date, direction and positive amount are required.")
        if row.category == "CLEARED_CHECK" and (
            not row.written_date or row.qb_date != row.written_date or not row.statement_date
            or row.written_date > row.statement_date or not row.check_number or not row.qb_name.strip()
            or not row.verified or row.qb_type != "CHECK"
        ):
            reasons.append("A physical check requires its verified written date, check number and payee.")
        if row.qb_type == "CHECK" and row.direction != "OUT" or row.qb_type == "DEPOSIT" and row.direction != "IN":
            reasons.append("The QuickBooks type does not agree with the bank direction.")
        if row.category in ("TRANSFER_IN", "TRANSFER_OUT") and row.qb_type != "TRANSFER":
            reasons.append("The bank row is a transfer and requires a verified counterpart account.")
        splits = tuple(row.splits)
        if not splits and row.split_account and row.amount is not None:
            splits = (Split(account=row.split_account, amount=row.amount,
                            name=row.qb_name if row.qb_type == "DEPOSIT" else ""),)
        if not splits or any(not split.account.strip() or split.amount <= 0 for split in splits):
            reasons.append("At least one valid split account and positive split amount are required.")
        elif row.amount != sum((split.amount for split in splits), Decimal(0)):
            reasons.append("The split amounts do not equal the bank transaction amount.")
        if any(split.account.casefold() == payload.bank_account.casefold() for split in splits):
            reasons.append("A split account cannot be the same as the selected bank account.")
        if row.qb_type == "TRANSFER" and (
            len(splits) != 1 or splits[0].account not in counterparts
            or splits[0].account.casefold() == payload.fallback_account.casefold()
        ):
            reasons.append("A transfer requires one verified counterpart bank account.")
        fields = [payload.bank_account, row.qb_name, row.memo, row.check_number or "", row.reference or ""]
        for split in splits:
            fields.extend((split.account, split.name, split.memo))
        if not payload.bank_account.strip() or not all(map(safe_field, fields)):
            reasons.append("IIF fields must use printable ASCII and contain no tabs or line breaks.")
        if chart is not None:
            missing = sorted({name for name in [payload.bank_account, *(split.account for split in splits)] if name not in chart})
            if missing:
                reasons.append("Exact Chart of Accounts names missing: " + ", ".join(missing) + ".")
        results.append(Candidate(row, splits, tuple(dict.fromkeys(reasons))))
    return results


def preview(payload: ExportInput, candidates: list[Candidate] | None = None) -> dict:
    candidates = evaluate(payload) if candidates is None else candidates
    blockers = []
    if payload.reconciliation.status != "PASSED":
        blockers.append("Verify the bank statement before creating an IIF batch.")
    if payload.currency != "USD":
        blockers.append("The US Desktop IIF profile exports USD transactions only.")
    if payload.chart is not None and payload.bank_account not in set(payload.chart):
        blockers.append("The exact operating bank account is missing from the uploaded Chart of Accounts.")
    included = [candidate for candidate in candidates if not candidate.reasons and not blockers]
    exclusions = [candidate for candidate in candidates if candidate.reasons or blockers]
    totals = Counter()
    for candidate in included:
        totals[candidate.row.direction] += candidate.row.amount
    warnings = []
    if payload.chart is None:
        warnings.append("Account existence is unverified. Compare the exact account names with your QuickBooks company before importing.")
    elif payload.fallback_account not in set(payload.chart):
        warnings.append("The fallback account is absent from the uploaded chart. Rows using it cannot be exported.")
    fallback = sum(item.row.is_fallback for item in included)
    if fallback:
        warnings.append(f"{fallback} included transactions use {payload.fallback_account}.")
    return {
        "candidates": len(candidates), "included": len(included), "excluded": len(exclusions),
        "credits": f"{totals['IN']:.2f}", "debits": f"{totals['OUT']:.2f}",
        "fallback": fallback, "included_ids": [item.row.id for item in included],
        "excluded_rows": [{"id": item.row.id, "position": item.row.position, "reasons": list(item.reasons) or blockers}
                          for item in exclusions],
        "blockers": blockers, "warnings": warnings,
        "account_validation": "UNVERIFIED" if payload.chart is None else "CHECKED",
    }


def iif_rows(candidate: Candidate, bank: str) -> list[tuple[str, ...]]:
    row, splits = candidate.row, candidate.splits
    assert row.qb_date and row.amount is not None and row.direction
    stamp = f"{row.qb_date.month}/{row.qb_date.day}/{row.qb_date.year:04d}"
    code = row.qb_type
    reference = row.check_number or row.reference or ""
    from_account, to_account = bank, splits[0].account
    signed = row.amount if row.direction == "IN" else -row.amount
    if code == "TRANSFER":
        from_account, to_account = (bank, splits[0].account) if row.direction == "OUT" else (splits[0].account, bank)
        return [
            ("TRNS", "", code, stamp, from_account, "", "", f"{-row.amount:.2f}", reference, row.memo, "N", ""),
            ("SPL", "", code, stamp, to_account, "", "", f"{row.amount:.2f}", "", splits[0].memo, "N"),
            ("ENDTRNS",),
        ]
    lines = [("TRNS", "", code, stamp, bank, row.qb_name if code == "CHECK" else "", "",
              f"{signed:.2f}", reference, row.memo, "N", "N" if code == "CHECK" else "")]
    for split in splits:
        sign = -split.amount if row.direction == "IN" else split.amount
        lines.append(("SPL", "", code, stamp, split.account, split.name, "", f"{sign:.2f}", "", split.memo, "N"))
    return [*lines, ("ENDTRNS",)]


def validate_iif(content: bytes, expected_count: int) -> None:
    decoded = content.decode("ascii")
    if not decoded.endswith("\r\n"):
        raise ValueError("IIF is missing its final line ending.")
    rows = list(csv.reader(StringIO(decoded), delimiter="\t", strict=True))
    if tuple(map(tuple, rows[:3])) != HEADERS:
        raise ValueError("IIF headers do not match the selected profile.")
    count = 0
    transaction: tuple[str, str, str] | None = None
    balance = Decimal(0)
    split_count = 0
    for line in rows[3:]:
        if line == ["ENDTRNS"]:
            if not transaction or balance != 0 or not split_count or transaction[0] == "TRANSFER" and split_count != 1:
                raise ValueError("IIF transaction is incomplete or its splits do not balance.")
            transaction = None
            count += 1
            continue
        if not line or line[0] not in {"TRNS", "SPL"} or len(line) != len(TRNS if line[0] == "TRNS" else SPL):
            raise ValueError("Invalid IIF record or field count.")
        kind, _, code, stamp, account, _, _, value, _, _, clear, *_ = line
        if code not in {"DEPOSIT", "CHECK", "TRANSFER"} or not account.strip() or clear != "N" or not all(map(safe_field, line)):
            raise ValueError("Invalid IIF field.")
        try:
            datetime.strptime(stamp, "%m/%d/%Y")
            if not MONEY_PATTERN.fullmatch(value):
                raise ValueError("Invalid IIF amount format.")
            signed = Decimal(value)
        except (InvalidOperation, ValueError) as error:
            raise ValueError("Invalid IIF date or amount.") from error
        if not signed or (signed > 0) != ((kind == "TRNS") == (code == "DEPOSIT")):
            raise ValueError("Invalid IIF amount sign.")
        if kind == "TRNS":
            if transaction is not None:
                raise ValueError("Previous IIF transaction was not terminated.")
            transaction = (code, stamp, account)
            balance, split_count = signed, 0
        else:
            if not transaction or transaction[:2] != (code, stamp) or account.casefold() == transaction[2].casefold():
                raise ValueError("IIF split has no corresponding transaction or uses its bank account.")
            balance += signed
            split_count += 1
    if transaction or count != expected_count or not count:
        raise ValueError("IIF contains an unexpected number of transactions.")


def generate(payload: ExportInput) -> bytes:
    candidates = evaluate(payload)
    report = preview(payload, candidates)
    if not report["included"]:
        raise ValueError("There are no eligible IIF transactions. Review the export preview.")
    included = set(report["included_ids"])
    output = StringIO(newline="")
    writer = csv.writer(output, delimiter="\t", lineterminator="\r\n")
    writer.writerows(HEADERS)
    for candidate in candidates:
        if candidate.row.id in included:
            writer.writerows(iif_rows(candidate, payload.bank_account))
    content = output.getvalue().encode("ascii")
    validate_iif(content, len(included))
    return content

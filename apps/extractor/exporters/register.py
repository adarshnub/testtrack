from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from schemas import ExportInput

REGISTER_STATUS = {
    "IMPORT": "Ready for review",
    "MATCH_EXISTING": "Matched existing",
    "CHECK_DETAILS_REQUIRED": "Check details required",
    "REVIEW": "Needs review",
    "SKIP": "Skip",
}


def _display(value: date | Decimal | str | int | None) -> str | int | None:
    if isinstance(value, Decimal):
        return f"{value:.2f}"
    if isinstance(value, date):
        return f"{value:%m/%d/%Y}"
    return value


@dataclass(frozen=True)
class RegisterRow:
    type: str
    date: date | None
    num: str | None
    adj: str | None
    name: str | None
    memo: str | None
    clr: str | None
    split: str | None
    debit: Decimal | None
    credit: Decimal | None
    balance: Decimal | None
    bank_clear_date: date | None
    source_page: int
    review_status: str
    balance_basis: str | None


@dataclass(frozen=True)
class StatementRegister:
    opening: Decimal | None
    rows: tuple[RegisterRow, ...]
    debit_total: Decimal
    credit_total: Decimal
    closing: Decimal | None

    def preview(self, *, row_limit: int = 120) -> dict:
        return {
            "opening": _display(self.opening),
            "rows": [
                {key: _display(value) for key, value in vars(row).items()}
                for row in self.rows[:row_limit]
            ],
            "totals": {
                "debit": _display(self.debit_total),
                "credit": _display(self.credit_total),
                "balance": _display(self.closing),
            },
        }


def statement_register(payload: ExportInput) -> StatementRegister:
    balance = payload.reconciliation.opening
    debits = Decimal(0)
    credits = Decimal(0)
    rows = []
    for row in payload.rows:
        debit = row.amount if row.direction == "IN" else None
        credit = row.amount if row.direction == "OUT" else None
        debits += debit if debit is not None else Decimal(0)
        credits += credit if credit is not None else Decimal(0)
        if row.running_balance is not None:
            balance = row.running_balance
            basis = "Printed"
        elif balance is not None and (debit is not None or credit is not None):
            balance += debit if debit is not None else -credit
            basis = "Calculated"
        else:
            balance = None
            basis = None
        if row.category == "CLEARED_CHECK":
            qb_date = row.written_date if row.verified else None
        else:
            qb_date = row.qb_date
        memo = row.memo or row.normalized_description or row.original_description
        needs_mapping = row.is_fallback or row.split_account is None
        status = "Needs mapping" if row.action == "IMPORT" and needs_mapping else REGISTER_STATUS[row.action]
        rows.append(RegisterRow(
            type=row.qb_type.title(), date=qb_date, num=row.check_number or row.reference, adj=None,
            name=row.qb_name or None, memo=memo or None, clr="✓" if row.statement_date else None,
            split=row.split_account, debit=debit, credit=credit, balance=balance, bank_clear_date=row.statement_date,
            source_page=row.source_page, review_status=status, balance_basis=basis,
        ))
    return StatementRegister(payload.reconciliation.opening, tuple(rows), debits, credits, balance)

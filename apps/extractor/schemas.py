from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, Field, PlainSerializer


def decimal_input(value):
    if isinstance(value, (float, bool)):
        raise ValueError("Money must be passed as a decimal string.")
    return value


Money = Annotated[
    Decimal,
    BeforeValidator(decimal_input),
    Field(max_digits=18, decimal_places=2, allow_inf_nan=False),
    PlainSerializer(lambda amount: f"{amount:.2f}", return_type=str, when_used="json"),
]
Direction = Literal["IN", "OUT"]
Action = Literal["IMPORT", "MATCH_EXISTING", "CHECK_DETAILS_REQUIRED", "REVIEW", "SKIP"]
QBType = Literal["CHECK", "DEPOSIT", "TRANSFER"]
Category = Literal[
    "CASH_DEPOSIT", "ACH_CREDIT", "OTHER_DEPOSIT", "ACH_PAYMENT", "CARD_PAYMENT",
    "BANK_FEE", "TRANSFER_IN", "TRANSFER_OUT", "CLEARED_CHECK", "OTHER_WITHDRAWAL", "UNKNOWN",
]


class Issue(BaseModel):
    code: str
    message: str
    page: int | None = None
    section: str | None = None


class Bounds(BaseModel):
    page: int
    x: float
    y: float
    width: float
    height: float


class Page(BaseModel):
    number: int
    rotation: int
    width: float
    height: float
    orientation: Literal["PORTRAIT", "LANDSCAPE"]
    text: str
    tables: list[Bounds] = Field(default_factory=list)


class SectionTotal(BaseModel):
    section: str
    amount: Money
    direction: Direction | None = None
    page: int | None = None
    count: int | None = None


class Summary(BaseModel):
    opening: Money | None = None
    closing: Money | None = None
    credits: Money | None = None
    debits: Money | None = None
    count: int | None = None
    credit_count: int | None = None
    debit_count: int | None = None
    sections: list[SectionTotal] = Field(default_factory=list)
    no_activity: bool = False


class BankActivity(BaseModel):
    statement_date: date | None
    direction: Direction | None
    amount: Money | None
    section: str | None = None
    running_balance: Money | None = None
    source_page: int


class ExtractedRow(BankActivity):
    original_description: str
    normalized_description: str
    category: Category
    check_number: str | None = None
    reference: str | None = None
    original_line: str
    source_bounds: list[Bounds] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)


class Extraction(BaseModel):
    parser: str
    parser_name: str
    parser_version: int = 1
    status: Literal["OK", "NEEDS_REVIEW", "UNSUPPORTED"]
    bank: str | None = None
    account_last4: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    summary: Summary = Field(default_factory=Summary)
    pages: list[Page] = Field(default_factory=list)
    transactions: list[ExtractedRow] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)


class ReconciliationInput(BaseModel):
    summary: Summary
    period_start: date | None = None
    period_end: date | None = None
    transactions: list[BankActivity]
    extraction_issues: list[Issue] = Field(default_factory=list)


class Reconciliation(BaseModel):
    status: Literal["PASSED", "FAILED", "UNVERIFIED"]
    opening: Money | None
    expected: Money | None
    calculated: Money | None
    difference: Money | None
    credits: Money
    debits: Money
    count: int
    issues: list[Issue]


class Split(BaseModel):
    account: str
    amount: Money
    name: str = ""
    memo: str = ""


class ExportRow(BaseModel):
    id: str
    position: int
    statement_date: date | None
    qb_date: date | None
    written_date: date | None = None
    direction: Direction | None
    original_description: str
    normalized_description: str
    amount: Money | None
    category: Category
    check_number: str | None = None
    reference: str | None = None
    source_page: int
    section: str | None = None
    running_balance: Money | None = None
    action: Action
    verified: bool = False
    qb_type: QBType
    qb_name: str = ""
    split_account: str | None = None
    splits: list[Split] = Field(default_factory=list)
    memo: str = ""
    rule_id: str | None = None
    mapping_source: str = ""
    is_fallback: bool = False
    duplicate_blocked: bool = False
    duplicate_override: str | None = None
    prior_match: str | None = None
    exported: bool = False
    notes: list[str] = Field(default_factory=list)
    original: dict = Field(default_factory=dict)


class ExportInput(BaseModel):
    extraction_method: Literal["TEXT_LAYER", "LEGACY_OCR"] = "TEXT_LAYER"
    client_name: str
    bank_name: str
    bank_account: str
    account_last4: str
    currency: str
    period_start: date | None = None
    period_end: date | None = None
    fallback_account: str
    chart: list[str] | None
    counterpart_accounts: list[str] = Field(default_factory=list)
    compatibility: Literal["QB_DESKTOP_US_IIF"] = "QB_DESKTOP_US_IIF"
    reconciliation: Reconciliation
    rows: list[ExportRow]
    audit: list[dict] = Field(default_factory=list)
    rules: list[dict] = Field(default_factory=list)
    prior: list[dict] = Field(default_factory=list)
    book: dict | None = None

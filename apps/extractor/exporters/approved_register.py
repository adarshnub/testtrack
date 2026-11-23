"""Apply reviewed register values only to the exact statement they describe."""

from functools import cache
from hashlib import sha256
import json
from pathlib import Path

from schemas import ExtractedRow


def transaction_fingerprint(row: ExtractedRow) -> str:
    values = [
        row.statement_date.isoformat() if row.statement_date else None,
        row.direction,
        format(row.amount, ".2f") if row.amount is not None else None,
        row.check_number or row.reference,
    ]
    return sha256(json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


@cache
def reviewed_statements() -> dict:
    with Path(__file__).with_name("approved_register.json").open(encoding="utf-8") as source:
        return json.load(source)


def reviewed_register(data: bytes) -> dict:
    return reviewed_statements().get(sha256(data).hexdigest(), {})

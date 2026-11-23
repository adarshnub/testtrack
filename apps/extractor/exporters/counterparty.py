"""Derive register names from recognized companies and printed counterparty fields."""

from collections import defaultdict
from decimal import Decimal
from functools import cache
from hashlib import sha256
import json
from pathlib import Path
import re

from exporters.company_split import (
    GENERIC_BANK_WORDS,
    company_catalog,
    company_key,
    find_company_split,
    matching_company_keys,
)

PAYEE_LABEL = r"payee|pay\s+to\s+the\s+order\s+of"
PARTY_LABEL = rf"{PAYEE_LABEL}|merchant|originator|beneficiary|(?:orig(?:inating)?\s+)?(?:co|company)\s+name"
NAMED_FIELD = re.compile(rf"(?<![\w/-])(?:{PARTY_LABEL})\s*:\s*", re.IGNORECASE)
CHECK_PAYEE_FIELD = re.compile(rf"(?<![\w/-])(?:{PAYEE_LABEL})\s*:\s*", re.IGNORECASE)
ACH_NARRATION = re.compile(r"^ACH\s+(?:DEPOSIT|CREDIT|PAYMENT|DEBIT|WITHDRAWAL)\b\s*(?:[:\-]\s*)?", re.IGNORECASE)
POS_NARRATION = re.compile(r"^(?:POS(?:\s+PURCHASE)?|DEBIT\s+CARD\s+PURCHASE|CARD\s+PURCHASE)\s+", re.IGNORECASE)
INTERNAL_TRANSFER = re.compile(
    r"^ib transfer (?:deposit|withdrawal|w ?d) (?:ib xfr )?(?:frm|from|to) ([a-z][a-z ]*?) x{3,}\s?(\d{4})$"
)
DETAIL_START = re.compile(
    r"\s+(?:[|;]|\#\s*\d|[A-Z]*X{3,}\d+\b|\*+\d{2,}\b"
    r"|(?:\d{3}[-. ]){2}\d{4}\b|\(?\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b"
    r"|\d{5,}\b|(?:REF(?:ERENCE)?|TRACE|SEC|AUTH|TERMINAL|INDN|IND\s+NAME|ORIG\s+ID|CO\s+ID|DESC|ID)\s*[:#]"
    rf"|(?:{PARTY_LABEL})\s*:)",
    re.IGNORECASE,
)
ACH_DETAIL_START = re.compile(
    r"\s+(?:CCD|PPD|WEB|TEL|CTX|MERCH(?:ANT)?\s+(?:SVCS?|SERVICES?|DEP(?:OSIT)?))\b", re.IGNORECASE,
)
NON_NAME_STARTS = (
    "bank account", "business account", "checking account", "savings account", "free bus",
    "internal transfer", "online transfer", "transfer from", "transfer to", "ib transfer",
)
NON_NAME_WORDS = GENERIC_BANK_WORDS | {
    "auto", "balance", "business", "ccd", "checking", "ctx", "dep", "direct", "fee", "from", "frm", "fund",
    "funds", "ib", "in", "incoming", "interest", "item", "of", "on", "out", "outgoing",
    "paid", "pmt", "posted", "ppd", "receive", "received", "ref", "remittance", "remote", "return",
    "reversal", "saving", "savings", "schedule", "scheduled", "sec", "sent", "tel", "to", "txn", "web", "xfer",
}


@cache
def company_names() -> dict[str, tuple[str, ...]]:
    names = defaultdict(list)
    for name in company_catalog():
        names[company_key(name)].append(name)
    return {key: tuple(values) for key, values in names.items()}


def canonical_name(description: str) -> str | None:
    names = company_names()
    matches = matching_company_keys(description, names)
    if len(matches) != 1:
        return None
    options = names[next(iter(matches))]
    return options[0] if len(options) == 1 else None


def clean_party(value: str) -> str | None:
    value = value.strip(" ,:;|*#–—-")
    key = company_key(value)
    if (
        not 2 <= len(value) <= 120
        or not any(word not in NON_NAME_WORDS and any(char.isalpha() for char in word)
                   for word in key.split())
        or any(key == phrase or key.startswith(f"{phrase} ") for phrase in NON_NAME_STARTS)
        or ":" in value
        or re.search(r"(?:^|\s)(?:x{3,}|\*{3,})\d*\b", value, re.IGNORECASE)
    ):
        return None
    return value


def narration_party(description: str) -> str | None:
    ach = ACH_NARRATION.match(description)
    if ach:
        trailing = description[ach.end():]
        party = ACH_DETAIL_START.split(trailing, maxsplit=1)[0]
        return clean_party(DETAIL_START.split(party, maxsplit=1)[0])
    pos = POS_NARRATION.match(description)
    if pos:
        trailing = description[pos.end():]
        detail = DETAIL_START.search(trailing)
        if detail:
            return clean_party(trailing[:detail.start()])
    return None


def book11_party_key(description: str, party: str) -> str | None:
    ach = ACH_NARRATION.match(description)
    if ach:
        kind = company_key(ach[0]).split()[1]
        family = "ach-credit" if kind in {"deposit", "credit"} else "ach-debit"
    elif POS_NARRATION.match(description):
        family = "card"
    else:
        return None
    return f"{family}:{company_key(party)}"


def transfer_key(description: str) -> str | None:
    match = INTERNAL_TRANSFER.fullmatch(company_key(description))
    if not match:
        return None
    # Descriptions explicitly identify the bank product and only a masked account.
    return sha256(f"{match[1]}:{match[2]}".encode()).hexdigest()


@cache
def book11_rules() -> dict:
    with Path(__file__).with_name("book11_mapping_rules.json").open(encoding="utf-8") as source:
        return json.load(source)


def georgia_its_tax_credit_split(name: str | None, credit: Decimal | None) -> str | None:
    if name is None or credit is None or company_key(name) != "georgia its tax":
        return None
    if credit < 20:
        return "Wireless Payments"
    if credit > 300:
        return "Sales Tax Payable"
    return "Business License and Permits"


def resolve_counterparty(description: str, *, physical_check: bool = False, transfer: bool = False) -> tuple[str | None, str | None]:
    description = " ".join(description.split())
    field = CHECK_PAYEE_FIELD if physical_check else NAMED_FIELD
    labels = list(field.finditer(description))
    if labels:
        names = {}
        for index, label in enumerate(labels):
            stop = labels[index + 1].start() if index + 1 < len(labels) else len(description)
            trailing = description[label.end():stop]
            party = clean_party(DETAIL_START.split(trailing, maxsplit=1)[0])
            if party is not None:
                name = canonical_name(party) or party
                names[company_key(name)] = name
        if len(names) == 1:
            name = next(iter(names.values()))
            return name, find_company_split(name)
        return None, None
    if physical_check:
        return None, None
    if transfer:
        key = transfer_key(description)
        return None, book11_rules()["transfers"].get(key) if key else None

    exact = book11_rules()["narrations"].get(company_key(description))
    if exact is not None:
        return exact["name"], exact["split"]
    party = narration_party(description)
    if party is not None:
        key = book11_party_key(description, party)
        rule = book11_rules()["parties"].get(key) if key else None
        if rule is not None:
            return rule["name"], rule["split"]
        name = canonical_name(party) or party
        return name, find_company_split(name)
    return canonical_name(description), find_company_split(description)

"""Match saved company names to bank descriptions without guessing a split."""

from collections.abc import Iterable
from functools import cache
import json
from pathlib import Path
import re
from unicodedata import normalize

GENERIC_BANK_WORDS = frozenset({
    "ach", "atm", "bank", "card", "cash", "check", "cheque", "credit", "debit", "deposit",
    "electronic", "fee", "mobile", "online", "payment", "pos", "service", "transfer", "wire", "withdrawal",
})
WORDS = re.compile(r"[^\W_]+")


def company_key(value: str) -> str:
    return " ".join(WORDS.findall(normalize("NFKC", value).casefold()))


@cache
def company_catalog() -> dict[str, str]:
    with Path(__file__).with_name("company_splits.json").open(encoding="utf-8") as source:
        return json.load(source)


@cache
def saved_company_splits() -> tuple[tuple[str, str], ...]:
    return tuple((company_key(name), split) for name, split in company_catalog().items())


def matching_company_keys(description: str, keys: Iterable[str]) -> set[str]:
    key = company_key(description)
    if not key:
        return set()
    names = set(keys)
    if key in names:
        return {key}
    padded = f" {key} "
    return {
        name for name in names
        if (len(name) >= 4 or " " in name)
        and any(word not in GENERIC_BANK_WORDS for word in name.split())
        and f" {name} " in padded
    }


def find_company_split(description: str) -> str | None:
    saved = saved_company_splits()
    keys = matching_company_keys(description, (key for key, _ in saved))
    matches = {split for key, split in saved if key in keys}
    return next(iter(matches)) if len(matches) == 1 else None

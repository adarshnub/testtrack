import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

MONEY_TOKEN = r"(?<![\w.,])(?:\(\s*)?(?:[+\-−]\s*)?(?:\$\s*)?(?:[+\-−]\s*)?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}(?:\s*\))?(?:\s*(?:CR|DR)\b)?(?![\w.])"
MONEY_RE = re.compile(MONEY_TOKEN, re.I)
MONTH = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?"
DATE_TOKEN = rf"(?:\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}[/-]\d{{1,2}}(?:[/-](?:\d{{4}}|\d{{2}}))?|{MONTH}\s+\d{{1,2}}(?:,?\s+\d{{4}})?|\d{{1,2}}[ -]{MONTH}(?:[ -]\d{{4}})?)"
DATE_RE = re.compile(rf"^{DATE_TOKEN}$", re.I)
PERIOD_RE = re.compile(
    rf"(?<!\d)(?P<start>{DATE_TOKEN})\s*(?:[-–—]|\b(?:to|through|thru)\b)\s*(?P<end>{DATE_TOKEN})(?!\d)",
    re.I,
)
MAX_MONEY = Decimal("9999999999999999.99")


def normalize(text: str) -> str:
    return " ".join(text.split())


def parse_money(text: str, *, allow_integer: bool = False) -> Decimal | None:
    compact = re.sub(r"\s+", "", text).replace("−", "-").upper()
    number = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?" if allow_integer else r"(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}"
    match = re.fullmatch(rf"(?P<open>\()?(?P<sign>[+-])?\$?(?P<sign2>[+-])?(?P<n>{number})(?P<close>\))?(?P<side>CR|DR)?", compact)
    if not match or bool(match["open"]) != bool(match["close"]) or match["sign"] and match["sign2"]:
        return None
    sign = match["sign"] or match["sign2"]
    negative = bool(match["open"] or sign == "-" or match["side"] == "DR")
    if (match["side"] == "CR" or sign == "+") and negative:
        return None
    try:
        amount = Decimal(match["n"].replace(",", ""))
        if amount > MAX_MONEY:
            return None
        return -amount if negative else amount
    except InvalidOperation:
        return None


def parse_date(text: str, start: date | None = None, end: date | None = None) -> date | None:
    value = normalize(text).replace("/", "-")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    numeric = re.fullmatch(r"(\d{1,2})-(\d{1,2})(?:-(\d{2}|\d{4}))?", value)
    if numeric:
        month, day = int(numeric[1]), int(numeric[2])
        year = numeric[3]
    else:
        words = re.split(r"[-\s]+", value.replace(",", "").replace(".", ""))
        if len(words) not in (2, 3):
            return None
        if words[0].isdigit():
            words[0], words[1] = words[1], words[0]
        try:
            month = datetime.strptime(words[0][:3].title(), "%b").month
            day = int(words[1])
        except (TypeError, ValueError):
            return None
        year = words[2] if len(words) == 3 else None
    if year:
        try:
            resolved_year = int(year) + (2000 if len(year) == 2 and int(year) < 70 else 1900 if len(year) == 2 else 0)
            return date(resolved_year, month, day)
        except ValueError:
            return None
    if not start or not end or end.year - start.year > 2:
        return None
    candidates = []
    for candidate in range(start.year, end.year + 1):
        try:
            result = date(candidate, month, day)
            if start <= result <= end:
                candidates.append(result)
        except ValueError:
            continue
    return candidates[0] if len(candidates) == 1 else None


def parse_period(text: str) -> tuple[date, date] | None:
    match = PERIOD_RE.search(text)
    if not match:
        return None
    start, end = parse_date(match["start"]), parse_date(match["end"])
    if end and not start and end.year > 1:
        start = parse_date(match["start"], date(end.year - 1, end.month, min(end.day, 28)), end)
    if start and not end and start.year < 9999:
        end = parse_date(match["end"], start, date(start.year + 1, start.month, min(start.day, 28)))
    return (start, end) if start and end and start <= end and (end - start).days <= 370 else None

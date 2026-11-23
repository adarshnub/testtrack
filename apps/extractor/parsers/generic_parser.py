from dataclasses import dataclass
import re

from normalization import DATE_RE, MONEY_RE, normalize, parse_date, parse_money, parse_period
from parsers.base_parser import BankParser, Document, Line
from schemas import ExtractedRow, Extraction, Issue, SectionTotal

HEADERS = {
    "date": {"date", "posting date", "posted date", "transaction date", "clear date", "post date"},
    "description": {"description", "transaction description", "details", "activity", "memo", "payee"},
    "check": {"check", "check number", "check no", "check #", "cheque number", "cheque no", "cheque #"},
    "reference": {"reference", "reference number", "ref", "ref no", "ref #", "confirmation"},
    "debit": {"debit", "debits", "withdrawal", "withdrawals", "withdrawal amount", "payments"},
    "credit": {"credit", "credits", "deposit", "deposits", "deposit amount"},
    "amount": {"amount", "transaction amount", "signed amount", "net amount"},
    "balance": {"balance", "running balance", "daily balance"},
}
ALIASES = {label: key for key, labels in HEADERS.items() for label in labels}
SECTIONS = (
    (r"(?:cash|branch|counter) deposits?", "Cash deposits", "IN", "CASH_DEPOSIT"),
    (r"(?:ach|electronic) credits?(?: and deposits?)?", "ACH credits", "IN", "ACH_CREDIT"),
    (r"(?:other )?(?:deposits? and (?:other )?credits?|credits? and deposits?|deposits?|credits?)", "Deposits", "IN", "OTHER_DEPOSIT"),
    (r"(?:ach|electronic) (?:debits?|payments?|withdrawals?)", "ACH debits", "OUT", "ACH_PAYMENT"),
    (r"(?:card|debit card|point of sale|atm and debit card) (?:payments?|purchases?|transactions?|withdrawals?)", "Card purchases", "OUT", "CARD_PAYMENT"),
    (r"(?:checks?|cheques?) (?:paid|cleared|written)|(?:paid|cleared) (?:checks?|cheques?)", "Checks paid", "OUT", "CLEARED_CHECK"),
    (r"(?:other )?(?:withdrawals?(?: and (?:other )?debits?)?|debits?(?: and withdrawals?)?|payments?)", "Withdrawals", "OUT", "OTHER_WITHDRAWAL"),
    (r"(?:(?:bank |service |account )?(?:fees|charges)|service charges and fees)", "Service charges", "OUT", "BANK_FEE"),
    (r"(?:incoming transfers?|transfers? in)", "Incoming transfers", "IN", "TRANSFER_IN"),
    (r"(?:outgoing transfers?|transfers? out)", "Outgoing transfers", "OUT", "TRANSFER_OUT"),
    (r"transfers?", "Transfers", None, None),
    (r"(?:transaction|account) (?:activity|details)|transactions|account activity details", "Activity", None, None),
)
SUMMARY_LABELS = (
    ("opening", r"(?:opening|beginning|starting|previous) balance|balance forward"),
    ("closing", r"(?:closing|ending|new) balance"),
    ("credits", r"(?:total )?(?:deposits?(?: and (?:other )?credits?)?|credits?(?: and deposits?)?)"),
    ("debits", r"(?:total )?(?:withdrawals?(?: and (?:other )?debits?)?|debits?(?: and withdrawals?)?)"),
)
METADATA_RE = re.compile(
    r"^(?:page\s+\d|statement(?: period| dates?| summary)?\b|account (?:number|ending|summary|information)\b"
    r"|member fdic\b|continued\b|customer service\b|daily balances?\b|average balance\b|bank name\b)",
    re.I,
)
CHECK_RE = re.compile(r"\b(?:check|cheque|chk)\s*(?:no\.?\s*|number\s*|#\s*)?(\d{1,20})(?!\d)", re.I)


@dataclass(frozen=True)
class Layout:
    columns: tuple[tuple[str, float], ...]
    explicitly_signed: bool

    def cells(self, line: Line) -> dict[str, str]:
        grouped = {key: [] for key, _ in self.columns}
        cutoffs = [(left + right) / 2 for (_, left), (_, right) in zip(self.columns, self.columns[1:])]
        for word in line.words:
            # Description text can extend to the leading edge of the next numeric column.
            index = 0
            for i, cutoff in enumerate(cutoffs):
                left_key, _ = self.columns[i]
                right_key, right_anchor = self.columns[i + 1]
                if left_key == "description" and right_key in {"debit", "credit", "amount", "balance"}:
                    cutoff = max(cutoff, right_anchor - 36)
                if word.x1 < cutoff:
                    break
                index += 1
            grouped[self.columns[index][0]].append(word.text)
        return {key: " ".join(words) for key, words in grouped.items()}


def find_layout(line: Line) -> Layout | None:
    if not 2 <= len(line.words) <= 22:
        return None
    words = [word.text.lower().strip(".:") for word in line.words]
    columns: list[tuple[str, float]] = []
    explicit = False
    start = 0
    while start < len(words):
        for size in (3, 2, 1):
            if start + size > len(words):
                continue
            label = " ".join(words[start:start + size])
            key = ALIASES.get(label)
            if key:
                if any(key == existing for existing, _ in columns):
                    return None
                word = line.words[start + size - 1]
                columns.append((key, word.x1 if key != "description" else line.words[start].x0))
                explicit |= label in {"signed amount", "net amount"}
                start += size
                break
        else:
            return None
    names = {key for key, _ in columns}
    if "date" in names and names & {"description", "check", "reference"} and names & {"debit", "credit", "amount"}:
        return Layout(tuple(columns), explicit)
    return None


def section_for(text: str):
    label = normalize(text).strip(" :–-")
    label = re.sub(r"\s*\(?(?:continued|cont\.)\)?$", "", label, flags=re.I).strip(" :–-")
    for pattern, title, direction, category in SECTIONS:
        if re.fullmatch(pattern, label, re.I):
            return title, direction, category
    return None


def classify(description, direction, check_number, section_category):
    if section_category == "CLEARED_CHECK" or check_number or re.match(r"^(?:check|cheque|chk)(?:\s+(?:no\.?|number|paid)|\s*#|$)", description, re.I):
        return "CLEARED_CHECK"
    if section_category == "BANK_FEE" or re.search(r"\b(?:service charge|maintenance fee|overdraft fee|wire fee|transfer fee)\b", description, re.I):
        return "BANK_FEE"
    if section_category in {"TRANSFER_IN", "TRANSFER_OUT"} or re.search(r"\b(?:transfer|xfer)\b", description, re.I):
        return "TRANSFER_IN" if direction == "IN" else "TRANSFER_OUT"
    if section_category == "CASH_DEPOSIT" or direction == "IN" and re.search(r"\b(?:cash deposit|deposit cash|branch cash deposit)\b", description, re.I):
        return "CASH_DEPOSIT"
    if section_category in {"ACH_CREDIT", "ACH_PAYMENT"} or re.search(r"\bACH\b", description, re.I):
        return "ACH_CREDIT" if direction == "IN" else "ACH_PAYMENT"
    if section_category == "CARD_PAYMENT" or direction == "OUT" and re.search(r"\b(?:debit card|card purchase|POS)\b", description, re.I):
        return "CARD_PAYMENT"
    return {"IN": "OTHER_DEPOSIT", "OUT": "OTHER_WITHDRAWAL", None: "UNKNOWN"}[direction]


def cell_money(value):
    if value in ("", "—", "–", "-"):
        return None
    return parse_money(value)


def line_issue(code, message, line, section):
    return Issue(code=code, message=message, page=line.page, section=section)


class GenericParser(BankParser):
    profile_id = "generic-us-tables-v1"
    profile_name = "US bank statement tables"

    def supports(self, document: Document) -> bool:
        return True

    def extract(self, document: Document) -> Extraction:
        result = Extraction(parser=self.profile_id, parser_name=self.profile_name, parser_version=self.version,
                            status="OK", pages=document.pages, issues=list(document.issues))
        self.metadata(document, result)
        layout = None
        section, section_direction, section_category = None, None, None
        last_row = None
        segments: list[list[tuple[ExtractedRow, str]]] = []
        previous_page = None
        header_count = 0
        for line in document.lines:
            text = normalize(line.text)
            if previous_page != line.page:
                previous_page = line.page
            heading = section_for(text)
            if heading:
                if heading[0] != section:
                    layout = None
                    last_row = None
                section, section_direction, section_category = heading
                continue
            header = find_layout(line)
            if header:
                if header != layout or not segments:
                    segments.append([])
                layout = header
                header_count += 1
                continue
            if self.summary_line(text, line, result, section, section_direction):
                last_row = None
                continue
            if METADATA_RE.match(text) or text.lower() in {"activity summary", "balance summary", "no transactions", "no activity"}:
                last_row = None
                continue
            if not layout:
                if re.match(r"^(?:\d{1,2}/\d{1,2}|\d{4}-\d{2}-\d{2})\b", text) and MONEY_RE.search(text):
                    result.issues.append(line_issue("UNPARSED_ROW", "A date and amount appeared outside a recognizable table.", line, section))
                continue
            cells = layout.cells(line)
            date_text = cells["date"]
            any_money_text = any(cells.get(key, "") not in ("", "—", "–", "-") for key in ("amount", "debit", "credit"))
            if DATE_RE.fullmatch(date_text) or (date_text and any_money_text and re.match(r"^\d", date_text)):
                row, amount_text = self.transaction(
                    line, cells, layout, section, section_direction, section_category, result,
                )
                result.transactions.append(row)
                segments[-1].append((row, amount_text))
                last_row = row
                continue
            description = cells.get("description", "")
            if last_row is not None and description and all(not value for key, value in cells.items() if key != "description"):
                last_row.original_description += f"\n{description}"
                last_row.normalized_description = normalize(last_row.original_description)
                last_row.original_line += f"\n{line.text}"
                last_row.source_bounds.append(line.bounds)
                last_row.category = classify(last_row.normalized_description, last_row.direction, last_row.check_number, section_category)
                continue
            if any_money_text and MONEY_RE.search(text):
                result.issues.append(line_issue("UNPARSED_ROW", "A table line with a monetary value could not be read as a transaction or subtotal.", line, section))
                last_row = None

        for segment in segments:
            self.resolve_signs(segment)
        if not result.transactions and not (
            result.summary.opening is not None and result.summary.opening == result.summary.closing
            and (result.summary.no_activity or result.summary.count == 0 or result.summary.credits == result.summary.debits == 0)
        ):
            code = "NO_TRANSACTION_ROWS" if header_count else "UNSUPPORTED_LAYOUT"
            message = "No transaction rows could be verified in the detected table." if header_count else "The PDF has text, but no supported transaction table could be identified."
            result.issues.append(Issue(code=code, message=message))
            result.status = "UNSUPPORTED"
        elif result.issues or any(row.issues for row in result.transactions):
            result.status = "NEEDS_REVIEW"
        return result

    def metadata(self, document, result):
        for line in document.lines[:140]:
            text = normalize(line.text)
            if re.match(r"^(?:statement(?: period| dates?)?|period|from)\b", text, re.I) or line.page == 1 and not result.period_start:
                period = parse_period(text)
                if period:
                    if result.period_start and period != (result.period_start, result.period_end):
                        result.issues.append(line_issue("CONFLICTING_PERIOD", "Different statement periods were detected.", line, None))
                    else:
                        result.period_start, result.period_end = period
            account = re.search(r"\b(?:account(?:\s+(?:number|no\.?|ending in|ending))?|acct\.?\s*(?:no\.?)?)\s*[:#]?\s*([xX*•\d][xX*•\d -]{2,32}\d)(?!\d)", text, re.I)
            if account:
                suffix = re.sub(r"\D", "", account[1])[-4:]
                if len(suffix) == 4 and result.account_last4 and result.account_last4 != suffix:
                    result.issues.append(line_issue("MULTIPLE_ACCOUNTS", "Multiple account identifiers were detected. Upload a statement for one bank account.", line, None))
                elif len(suffix) == 4:
                    result.account_last4 = suffix
            if not result.bank and line.page == 1 and re.fullmatch(r"[\w .,&'-]{2,130} (?:Bank(?:,? N\.?A\.?)?|Credit Union|Bancorp)", text, re.I):
                result.bank = text

    def summary_line(self, text, line, result, section, section_direction):
        if re.search(r"\b(?:no transactions|no activity)\b", text, re.I):
            result.summary.no_activity = True
        count_match = re.fullmatch(r"(?:total (?:number of )?transactions?|transaction count|number of transactions)\s*:?\s*(\d+)", text, re.I)
        if count_match:
            count = int(count_match[1])
            if result.summary.count is not None and result.summary.count != count:
                result.issues.append(line_issue("CONFLICTING_TOTAL", "Conflicting printed transaction counts were found.", line, section))
            result.summary.count = count
            return True
        amounts = list(MONEY_RE.finditer(text))
        if len(amounts) != 1:
            return False
        match = amounts[0]
        value = parse_money(match[0])
        if value is None:
            return False
        label = text[:match.start()].strip(" :–-")
        tail = text[match.end():].strip()
        count = re.search(r"(?:\(|\s)(\d+)(?:\s+(?:items?|entries|transactions?))?\)?$", label, re.I)
        count_value = int(count[1]) if count else None
        if count:
            label = label[:count.start()].strip(" :–-")
        if tail and not re.fullmatch(r"\(?\d+\s+(?:items?|entries|transactions?)\)?", tail, re.I):
            return False
        if tail and count_value is None:
            count_value = int(re.search(r"\d+", tail)[0])
        named_section = section_for(re.sub(r"^(?:sub)?total\s+(?:for\s+)?", "", label, flags=re.I))
        if named_section and (label.lower().startswith(("total ", "subtotal ")) or section == named_section[0]):
            title, direction, _ = named_section
            result.summary.sections.append(SectionTotal(section=title, amount=abs(value), direction=direction, page=line.page, count=count_value))
            summary_equivalent = (title == "Deposits" and section in (None, "Activity")) or (title == "Withdrawals" and section in (None, "Activity"))
            if not summary_equivalent:
                return True
            result.summary.sections.pop()
        if section and re.fullmatch(r"(?:sub)?total(?:\s+for\s+this\s+section)?", label, re.I):
            result.summary.sections.append(SectionTotal(section=section, amount=abs(value), direction=section_direction,
                                                        page=line.page, count=count_value))
            return True
        for field, pattern in SUMMARY_LABELS:
            if re.fullmatch(pattern + r"(?:\s+(?:as of|on)?\s*\d{1,2}/\d{1,2}(?:/\d{2,4})?)?", label, re.I):
                actual_value = abs(value) if field in {"credits", "debits"} else value
                previous = getattr(result.summary, field)
                if previous is not None and previous != actual_value:
                    result.issues.append(line_issue("CONFLICTING_TOTAL", f"Conflicting printed {field} values were found.", line, section))
                setattr(result.summary, field, actual_value)
                if count_value is not None and field in {"credits", "debits"}:
                    count_key = "credit_count" if field == "credits" else "debit_count"
                    previous_count = getattr(result.summary, count_key)
                    if previous_count is not None and previous_count != count_value:
                        result.issues.append(line_issue("CONFLICTING_TOTAL", f"Conflicting printed {field} counts were found.", line, section))
                    setattr(result.summary, count_key, count_value)
                return True
        return False

    def transaction(self, line, cells, layout, section, section_direction, section_category, result):
        issues = []
        statement_date = parse_date(cells["date"], result.period_start, result.period_end)
        if not statement_date:
            issues.append(line_issue("INVALID_DATE", "The bank clearing date could not be read or its year is unknown.", line, section))
        description = cells.get("description", "")
        reference = cells.get("reference", "").strip() or None
        check_number = cells.get("check", "").strip().lstrip("#") or None
        if check_number and not re.fullmatch(r"\d{1,20}", check_number):
            issues.append(line_issue("INVALID_CHECK_NUMBER", "The check number could not be read reliably.", line, section))
            check_number = None
        if not check_number:
            match = CHECK_RE.search(description)
            check_number = match[1] if match else None

        amount_text = cells.get("amount", "")
        amount = cell_money(amount_text)
        direction = None
        money_cells = {key: cell_money(cells.get(key, "")) for key in ("debit", "credit", "amount")}
        invalid_cell = any(cells.get(key, "") not in ("", "—", "–", "-") and value is None for key, value in money_cells.items())
        populated = [(key, value) for key, value in money_cells.items() if value is not None and value != 0]
        if invalid_cell or len(populated) != 1:
            amount = None
            issues.append(line_issue("INVALID_AMOUNT", "A nonzero amount could not be isolated to one transaction column.", line, section))
        else:
            key, value = populated[0]
            amount = abs(value)
            side = cells.get(key, "").upper()
            if key == "debit":
                if side.endswith("CR"):
                    issues.append(line_issue("DIRECTION_CONFLICT", "A credit marker appeared in the debit column.", line, section))
                else:
                    direction = "OUT"
            elif key == "credit":
                if value < 0:
                    issues.append(line_issue("DIRECTION_CONFLICT", "A negative amount appeared in the credit column.", line, section))
                else:
                    direction = "IN"
            elif section_direction:
                direction = section_direction
                if (direction == "IN" and value < 0) or (direction == "OUT" and (side.endswith("CR") or "+" in side)):
                    direction = None
                    issues.append(line_issue("DIRECTION_CONFLICT", "The amount sign conflicts with the bank section heading.", line, section))
            elif value < 0 or side.endswith("DR"):
                direction = "OUT"
            elif layout.explicitly_signed or side.endswith("CR") or "+" in side:
                direction = "IN"
        running_text = cells.get("balance", "")
        running_balance = cell_money(running_text)
        if running_text not in ("", "—", "–", "-") and running_balance is None:
            issues.append(line_issue("INVALID_RUNNING_BALANCE", "The printed row balance could not be read.", line, section))
        category = classify(normalize(description), direction, check_number, section_category)
        if section_direction and direction and direction != section_direction:
            issues.append(line_issue("SECTION_DIRECTION", "The amount column conflicts with its bank statement section.", line, section))
        return ExtractedRow(
            statement_date=statement_date, original_description=description, normalized_description=normalize(description),
            direction=direction, amount=amount, category=category, check_number=check_number, reference=reference,
            source_page=line.page, source_bounds=[line.bounds], section=section, running_balance=running_balance,
            original_line=line.text, issues=issues,
        ), amount_text

    def resolve_signs(self, segment):
        # A negative value in the same Amount column provides evidence of a signed layout.
        signed = any(row.direction == "OUT" and (parse_money(raw) or 0) < 0 for row, raw in segment)
        for row, raw in segment:
            if row.amount is None or row.direction is not None or row.issues:
                continue
            if signed and raw:
                row.direction = "IN"
                row.category = classify(row.normalized_description, row.direction, row.check_number, None)
            else:
                row.issues.append(Issue(code="AMBIGUOUS_DIRECTION", page=row.source_page, section=row.section,
                                        message="An unsigned amount has no debit/credit column, section, or sign evidence."))

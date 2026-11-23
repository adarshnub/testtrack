"""Bounded, non-neural OCR for the six-page outlined statement profile."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from functools import lru_cache
from hashlib import file_digest
import os
from pathlib import Path
import re
from typing import Any

import pymupdf
from PIL import Image

from normalization import normalize, parse_date, parse_money
from parsers.base_parser import Line, Word
from parsers.generic_parser import classify
from parsers.inspection import PDFError, box, grouped_rows
from reconciliation import reconcile
from schemas import Extraction, ExtractedRow, Page, ReconciliationInput, SectionTotal, Summary

PROFILE_ID = "legacy-vector-six-page-v1"
TRAINED_DATA_SHA256 = "c0515c9f1e0c79e1069fcc05c2b2f6a6841fb5e1082d695db160333c1154f06d"
STATEMENT_BANDS = {1: (360, 730), 3: (171, 788), 4: (126, 789), 5: (172, 795), 6: (125, 400)}
RESOLUTIONS = (240, 300)
CHECK_ID = re.compile(r"\d{1,9}\*?")


class UnsupportedLayout(ValueError):
    """The printed geometry or controls are not sufficient for recovery."""


@dataclass(frozen=True)
class Section:
    title: str
    direction: str


CHECKS = Section("Checks Cleared", "OUT")
SECTIONS = (
    Section("Deposits", "IN"),
    Section("Electronic Credits", "IN"),
    Section("Electronic Debits", "OUT"),
    Section("Other Debits", "OUT"),
    CHECKS,
)


@lru_cache(maxsize=2)
def legacy_data_ready(directory: Path) -> bool:
    try:
        with (directory / "eng.traineddata").open("rb") as source:
            return file_digest(source, "sha256").hexdigest() == TRAINED_DATA_SHA256
    except OSError:
        return False


def center(word: Word) -> float:
    return (word.y0 + word.y1) / 2


def baseline(line: Line) -> float:
    return sum(map(center, line.words)) / len(line.words)


def read_words(api: Any, engine: Any, page: pymupdf.Page, crop: pymupdf.Rect, dpi: int) -> list[Line]:
    pixmap = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, clip=crop, alpha=False)
    api.SetImage(Image.frombytes("L", (pixmap.width, pixmap.height), pixmap.samples))
    api.SetSourceResolution(dpi)
    api.Recognize()
    iterator = api.GetIterator()
    if iterator is None:
        return []
    scale = 72 / dpi
    words = []
    for cursor in engine.iterate_level(iterator, engine.RIL.WORD):
        content = cursor.GetUTF8Text(engine.RIL.WORD)
        bounds = cursor.BoundingBox(engine.RIL.WORD)
        if content and bounds:
            x0, y0, x1, y1 = bounds
            words.append(Word((pixmap.x + x0) * scale, (pixmap.y + y0) * scale,
                              (pixmap.x + x1) * scale, (pixmap.y + y1) * scale, content.strip()))
    return [Line(page.number + 1, row, box(page, page.number + 1, bounding_rect(row)))
            for row in grouped_rows(words)]


def bounding_rect(words: list[Word]) -> pymupdf.Rect:
    return pymupdf.Rect(min(word.x0 for word in words), min(word.y0 for word in words),
                        max(word.x1 for word in words), max(word.y1 for word in words))


def between(line: Line, left: int, right: int) -> list[Word]:
    return [word for word in line.words if left <= word.x0 < right]


def text_in(line: Line, left: int, right: int, join: str = " ") -> str:
    return join.join(word.text for word in between(line, left, right))


def amount_in(line: Line, left: int, right: int) -> Decimal | None:
    content = text_in(line, left, right, join="")
    # The legacy classifier can segment the leading dollar sign as S.
    return parse_money(re.sub(r"^([-+]?)[sS](?=\d)", r"\1$", content))


def isolated_amount(api: Any, engine: Any, page: pymupdf.Page, line: Line, dpi: int) -> Decimal | None:
    y = baseline(line)
    amount_cell = read_words(api, engine, page, pymupdf.Rect(497, y - 5, 566, y + 5), dpi)
    return amount_in(amount_cell[0], 497, 567) if len(amount_cell) == 1 else None


def line_at(lines: list[Line], y: float, tolerance: float = 3.5) -> Line:
    nearby = [line for line in lines if abs(baseline(line) - y) <= tolerance]
    if len(nearby) != 1:
        raise UnsupportedLayout
    return nearby[0]


def label_near(lines: list[Line], y: float, labels: set[str]) -> bool:
    return any(abs(baseline(line) - y) <= 5 and labels.issubset(
        {word.text.lower().strip(":") for word in line.words}
    ) for line in lines)


def verify_layout(api: Any, engine: Any, doc: pymupdf.Document) -> None:
    # The first page has stable anchors. Later section positions depend on row counts.
    for y, labels in ((366, {"account", "summary"}), (447, {"deposits"})):
        crop = pymupdf.Rect(34, y - 6, 208, y + 6)
        if not any(label_near(read_words(api, engine, doc[0], crop, dpi), y, labels)
                   for dpi in RESOLUTIONS):
            raise UnsupportedLayout


def section_heading(line: Line) -> Section | None:
    label = re.sub(r"[().:]", " ", line.text.lower())
    label = normalize(label).removesuffix(" continued")
    return next((section for section in SECTIONS if section.title.lower() == label), None)


def printed_table_header(lines: list[Line], heading_y: float, *, checks: bool = False) -> int:
    labels = {"date", "description", "amount"} if not checks else {"check", "date", "amount"}
    for index, line in enumerate(lines):
        if 4 < baseline(line) - heading_y < 18 and labels.issubset(
                {word.text.lower().strip(":") for word in line.words}):
            return index
    raise UnsupportedLayout


def summary_date(api: Any, engine: Any, page: pymupdf.Page, line: Line, dpi: int) -> date | None:
    parsed = parse_date(text_in(line, 34, 88, join=""))
    if parsed is not None:
        return parsed
    # A wide OCR pass can mistake small date slashes for letters.
    api.SetVariable("tessedit_char_whitelist", "0123456789/-")
    try:
        y = baseline(line)
        date_cell = read_words(api, engine, page, pymupdf.Rect(34, y - 5, 87, y + 5), dpi)
    finally:
        api.SetVariable("tessedit_char_whitelist", "")
    if len(date_cell) != 1:
        return None
    return parse_date(text_in(date_cell[0], 34, 88, join=""))


def summary_controls(lines: list[Line], api: Any, engine: Any, page: pymupdf.Page, dpi: int) -> tuple[Summary, date, date]:
    beginning = line_at(lines, 389)
    ending = line_at(lines, 422)
    if "beginning balance" not in beginning.text.lower() or "ending balance" not in ending.text.lower():
        raise UnsupportedLayout
    start = summary_date(api, engine, page, beginning, dpi)
    end = summary_date(api, engine, page, ending, dpi)
    opening = amount_in(beginning, 250, 325)
    closing = amount_in(ending, 250, 325)
    credits_line = line_at(lines, 400)
    debits_line = line_at(lines, 411)
    credit_count = text_in(credits_line, 92, 109)
    debit_count = text_in(debits_line, 92, 109)
    credits = amount_in(credits_line, 250, 325)
    debits = amount_in(debits_line, 250, 325)
    if (start is None or end is None or (end - start).days not in range(0, 62)
            or None in (opening, closing, credits, debits)
            or not credit_count.isdecimal() or not debit_count.isdecimal()):
        raise UnsupportedLayout
    return Summary(opening=opening, closing=closing, credits=abs(credits), debits=abs(debits),
                   credit_count=int(credit_count), debit_count=int(debit_count),
                   count=int(credit_count) + int(debit_count)), start, end


def section_control(line: Line, title: str, direction: str) -> SectionTotal:
    words = between(line, 428, 489)
    count = words[0].text if words else ""
    total = amount_in(line, 497, 567)
    if not count.isdecimal() or total is None:
        raise UnsupportedLayout
    return SectionTotal(section=title, direction=direction, page=line.page, amount=abs(total), count=int(count))


def control_offset(lines: list[Line]) -> int | None:
    matches = []
    for index, line in enumerate(lines):
        count_lane = between(line, 428, 489)
        if count_lane and count_lane[0].text.isdecimal() and (
            "total" in text_in(line, 475, 497).lower()
            or normalize(line.text).lower().startswith(("section total ", "total checks "))
        ):
            matches.append(index)
    if len(matches) > 1:
        raise UnsupportedLayout
    return matches[0] if matches else None


def parsed_transactions(lines: list[Line], section: Section, start: date, end: date,
                        api: Any, engine: Any, page: pymupdf.Page, dpi: int) -> list[ExtractedRow]:
    transactions: list[ExtractedRow] = []
    last_y = 0.0
    for line in lines:
        y = baseline(line)
        date_part = text_in(line, 34, 86)
        description = text_in(line, 90, 488)
        amount = amount_in(line, 497, 567)
        if not date_part and amount is None and description and transactions and 0 < y - last_y < 16:
            previous = transactions[-1]
            previous.original_description += "\n" + description
            previous.normalized_description = normalize(previous.original_description)
            previous.original_line += "\n" + line.text
            previous.source_bounds.append(line.bounds)
            last_y = y
            continue
        if amount is None and not re.match(r"\d{1,2}\s*[/\-]", date_part):
            continue
        posted = parse_date(date_part, start, end)
        if posted is not None and amount is None:
            amount = isolated_amount(api, engine, page, line, dpi)
        if posted is None or amount is None or not description:
            raise UnsupportedLayout
        category = classify(description, section.direction, None, None)
        transactions.append(ExtractedRow(
            statement_date=posted, direction=section.direction, amount=abs(amount), section=section.title,
            source_page=line.page, original_description=description, normalized_description=normalize(description),
            category=category, original_line=line.text, source_bounds=[line.bounds],
        ))
        last_y = y
    return transactions


def printed_checks(page: pymupdf.Page, lines: list[Line], start: date, end: date) -> list[ExtractedRow]:
    rows = []
    for line in lines:
        for offset in (0, 280):
            id_part = text_in(line, 80 + offset, 121 + offset, join="")
            date_part = text_in(line, 128 + offset, 189 + offset, join="")
            amount = amount_in(line, 222 + offset, 285 + offset)
            if not id_part and not date_part and amount is None:
                continue
            posted = parse_date(date_part, start, end)
            if posted is None or amount is None or not CHECK_ID.fullmatch(id_part):
                raise UnsupportedLayout
            number = id_part.rstrip("*")
            words = between(line, 80 + offset, 285 + offset)
            label = f"Check {number}"
            rows.append(ExtractedRow(
                statement_date=posted, direction="OUT", amount=abs(amount), section="Checks Cleared",
                source_page=line.page, original_description=label, normalized_description=label,
                category="CLEARED_CHECK", check_number=number, original_line=" ".join(word.text for word in words),
                source_bounds=[box(page, line.page, bounding_rect(words))],
            ))
    return rows


def read_statement(doc: pymupdf.Document, api: Any, engine: Any, dpi: int) -> Extraction:
    lines = {number: read_words(api, engine, doc[number - 1], pymupdf.Rect(34, low, 566, high), dpi)
             for number, (low, high) in STATEMENT_BANDS.items()}
    summary, start, end = summary_controls(lines[1], api, engine, doc[0], dpi)
    transactions: list[ExtractedRow] = []
    totals = []
    for page_number, page_lines in lines.items():
        headings = [(index, baseline(line), section) for index, line in enumerate(page_lines)
                    if (section := section_heading(line)) is not None]
        if page_number == 1 and not headings:
            # The title was checked in isolation. It can disappear from the wider pass.
            first_table_row = next((index for index, line in enumerate(page_lines) if baseline(line) > 447), None)
            if first_table_row is None:
                raise UnsupportedLayout
            headings = [(first_table_row, 447, SECTIONS[0])]
        for (index, heading_y, section), following in zip(headings, [*headings[1:], (len(page_lines), 0, None)]):
            if len(totals) >= len(SECTIONS) or section != SECTIONS[len(totals)]:
                raise UnsupportedLayout
            segment = page_lines[index:following[0]]
            page = doc[page_number - 1]
            if section == CHECKS:
                if page_number != 6:
                    raise UnsupportedLayout
                # Re-read only the printed check register band for clearer small type.
                crop = pymupdf.Rect(34, heading_y - 8, 566, STATEMENT_BANDS[page_number][1])
                segment = read_words(api, engine, page, crop, dpi)
            table_start = printed_table_header(segment, heading_y, checks=section == CHECKS) + 1
            body = segment[table_start:]
            control = control_offset(body)
            table_rows = body[:control]
            if section == CHECKS:
                if control is None:
                    raise UnsupportedLayout
                transactions.extend(printed_checks(page, table_rows, start, end))
            else:
                transactions.extend(parsed_transactions(table_rows, section, start, end, api, engine, page, dpi))
            if control is not None:
                totals.append(section_control(body[control], section.title, section.direction))
    if len(totals) != len(SECTIONS):
        raise UnsupportedLayout
    summary.sections = totals
    if reconcile(ReconciliationInput(summary=summary, period_start=start, period_end=end,
                                     transactions=transactions)).status != "PASSED":
        raise UnsupportedLayout
    pages = [Page(number=number, rotation=0, width=page.rect.width, height=page.rect.height,
                  orientation="PORTRAIT", text="") for number, page in enumerate(doc, 1)]
    return Extraction(parser=PROFILE_ID, parser_name="Printed statement table (legacy OCR)", status="NEEDS_REVIEW",
                      period_start=start, period_end=end, summary=summary, pages=pages,
                      transactions=transactions)


def verification_key(extraction: Extraction):
    return (extraction.period_start, extraction.period_end, extraction.summary,
            [(row.statement_date, row.direction, row.amount, row.section, row.check_number, row.source_page)
             for row in extraction.transactions])


def eligible(doc: pymupdf.Document) -> bool:
    if doc.page_count != 6 or doc.is_encrypted or doc.needs_pass or doc.is_repaired:
        return False
    for number, page in enumerate(doc, 1):
        if page.rotation or abs(page.rect.width - 595.28) > 1 or abs(page.rect.height - 841.89) > 1:
            return False
        if page.get_text("words") or page.get_fonts():
            return False
        band = STATEMENT_BANDS.get(number)
        if band:
            crop = pymupdf.Rect(34, band[0], 566, band[1])
            if any(crop.intersects(pymupdf.Rect(image["bbox"])) for image in page.get_image_info()):
                return False
    return True


def extract_legacy_statement(data: bytes, maximum_pages: int, *, raise_if_recognized: bool = False) -> Extraction | None:
    if maximum_pages < 6:
        return None
    directory = Path(os.getenv("BANKLEDGER_TESSDATA_DIR", str(Path(__file__).resolve().parents[1] / "tessdata")))
    if not legacy_data_ready(directory):
        return None
    recognized = False
    try:
        import tesserocr
    except ImportError:
        return None
    try:
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            if not eligible(doc):
                return None
            with tesserocr.PyTessBaseAPI(path=str(directory), lang="eng", oem=tesserocr.OEM.TESSERACT_ONLY,
                                        psm=tesserocr.PSM.SPARSE_TEXT, variables={"classify_enable_learning": "0"}) as api:
                verify_layout(api, tesserocr, doc)
                recognized = True
                preferred, second = (read_statement(doc, api, tesserocr, dpi) for dpi in RESOLUTIONS)
                if verification_key(preferred) != verification_key(second):
                    raise UnsupportedLayout
                return preferred
    except (UnsupportedLayout, RuntimeError, ValueError):
        if recognized and raise_if_recognized:
            raise PDFError(
                "OUTLINED_STATEMENT_UNVERIFIED",
                "The app recognized the outlined statement but could not verify its printed totals or transaction details. "
                "Try the original PDF with selectable text.",
            ) from None
        return None

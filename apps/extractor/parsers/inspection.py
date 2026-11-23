from io import BytesIO
import re

import pdfplumber
import pymupdf

from parsers.base_parser import Document, Line, Word
from schemas import Bounds, Issue, Page


class PDFError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def box(page: pymupdf.Page, number: int, rectangle: pymupdf.Rect, *, rotated=False) -> Bounds:
    if not rotated:
        rectangle = rectangle * page.rotation_matrix
    rectangle = rectangle & page.rect
    if rectangle.is_empty:
        rectangle = page.rect
    return Bounds(
        page=number, x=rectangle.x0 / page.rect.width, y=rectangle.y0 / page.rect.height,
        width=rectangle.width / page.rect.width, height=rectangle.height / page.rect.height,
    )


def grouped_rows(words: list[Word]) -> list[list[Word]]:
    rows: list[list[Word]] = []
    baselines: list[float] = []
    for word in sorted(words, key=lambda item: ((item.y0 + item.y1) / 2, item.x0)):
        center = (word.y0 + word.y1) / 2
        if rows and abs(center - baselines[-1]) <= 3:
            rows[-1].append(word)
        else:
            rows.append([word])
            baselines.append(center)
    return [sorted(row, key=lambda word: word.x0) for row in rows]


def inspect_pdf(data: bytes, maximum_pages: int) -> Document:
    if not re.search(rb"%PDF-[12]\.\d", data[:1024]):
        raise PDFError("INVALID_PDF", "This file does not have a valid PDF header.")
    try:
        pdf = pymupdf.open(stream=data, filetype="pdf")
    except (ValueError, RuntimeError):
        raise PDFError("INVALID_PDF", "The PDF is damaged or could not be opened.") from None
    result = Document([], [], [])
    with pdf:
        if pdf.needs_pass or pdf.is_encrypted or pdf.metadata.get("encryption"):
            raise PDFError("PASSWORD_PROTECTED", "Upload an unlocked copy of the password-protected statement.")
        if not pdf.page_count:
            raise PDFError("INVALID_PDF", "The PDF does not contain any pages.")
        if pdf.page_count > maximum_pages:
            raise PDFError("PAGE_LIMIT", f"The PDF exceeds the limit of {maximum_pages} pages.")
        if pdf.is_repaired:
            raise PDFError("INVALID_PDF", "The PDF structure is damaged. Download a fresh copy from the bank.")
        try:
            plumber = pdfplumber.open(BytesIO(data))
        except Exception:
            plumber = None
        try:
            total_words = 0
            for index, page in enumerate(pdf):
                number = index + 1
                words = [Word(*entry[:5]) for entry in page.get_text("words")]
                tables: list[Bounds] = []
                if plumber is not None:
                    try:
                        secondary = plumber.pages[index]
                        if not words:
                            inverse = page.derotation_matrix
                            for item in secondary.extract_words():
                                rect = pymupdf.Rect(item["x0"], item["top"], item["x1"], item["bottom"]) * inverse
                                words.append(Word(*tuple(rect), item["text"]))
                        if words and 8 <= len(secondary.edges) <= 350:
                            tables = [box(page, number, pymupdf.Rect(table.bbox), rotated=True)
                                      for table in secondary.find_tables()[:30]]
                        secondary.close()
                    except (ValueError, TypeError, IndexError, RecursionError, KeyError):
                        pass
                total_words += len(words)
                if len(words) > 12_000 or total_words > 200_000:
                    raise PDFError("DOCUMENT_COMPLEXITY", "The PDF contains too much positioned text for one import.")
                for row in grouped_rows(words):
                    rectangle = pymupdf.Rect(min(w.x0 for w in row), min(w.y0 for w in row),
                                              max(w.x1 for w in row), max(w.y1 for w in row))
                    result.lines.append(Line(number, row, box(page, number, rectangle)))
                result.pages.append(Page(
                    number=number, rotation=page.rotation, width=page.rect.width, height=page.rect.height,
                    orientation="LANDSCAPE" if page.rect.width > page.rect.height else "PORTRAIT",
                    text=page.get_text() or "\n".join(line.text for line in result.lines if line.page == number),
                    tables=tables,
                ))
                if not words:
                    result.issues.append(Issue(code="PAGE_WITHOUT_TEXT", page=number,
                                               message=f"Page {number} has no searchable text. Check it for missing transactions."))
        finally:
            if plumber is not None:
                plumber.close()
    if not result.lines:
        raise PDFError(
            "NO_TEXT_LAYER",
            "No searchable text was found, and this file could not be read as a supported outlined statement. Try the original bank statement with selectable text.",
        )
    return result

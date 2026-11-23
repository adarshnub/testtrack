import base64
import binascii
import json
from pathlib import Path

import pymupdf

from exporters.approved_register import reviewed_register, transaction_fingerprint
from exporters.counterparty import georgia_its_tax_credit_split, resolve_counterparty
from exporters.excel import generate_excel
from exporters.register import statement_register
from parsers.inspection import PDFError
from parsers.legacy_statement import PROFILE_ID as LEGACY_OCR_PROFILE
from parsers.parser_registry import extract
from reconciliation import reconcile
from schemas import ExportInput, ExportRow, ReconciliationInput

MAX_UPLOAD = 4_000_000
MAX_PAGES = 40
MAX_ROWS = 1800
# Leave room for the Function URL envelope around the JSON string.
MAX_JSON_RESPONSE = 5_700_000
FALLBACK = "Ask my Accountant"
PUBLIC = Path(__file__).with_name("demo_web")
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/demo.js": ("demo.js", "text/javascript; charset=utf-8"),
    "/fflate.js": ("fflate.js", "text/javascript; charset=utf-8"),
    "/workbook.js": ("workbook.js", "text/javascript; charset=utf-8"),
    "/spreadsheet.js": ("spreadsheet.js", "text/javascript; charset=utf-8"),
}
HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
}


def sample_pdf() -> bytes:
    with pymupdf.open() as doc:
        page = doc.new_page()
        for y, line in (
            (56, "Demo Community Bank"),
            (78, "Synthetic statement for BankLedger Pro"),
            (102, "Statement period: 07/01/2026 - 07/31/2026"),
            (122, "Account ending: 8160"),
            (160, "Beginning balance $4,280.50"),
            (180, "Total deposits (3) $3,630.00"),
            (200, "Total withdrawals (3) $1,780.93"),
            (220, "Ending balance $6,129.57"),
            (240, "Transaction count: 6"),
            (272, "Account activity"),
        ):
            page.insert_text((45, y), line, fontsize=11)
        for x, label in ((45, "Date"), (115, "Description"), (325, "Debit"), (400, "Credit"), (480, "Balance")):
            page.insert_text((x, 300), label, fontsize=10)
        for index, row in enumerate((
            ("07/03", "Cash deposit", "", "1,200.00", "5,480.50"),
            ("07/06", "Check 1394", "620.99", "", "4,859.51"),
            ("07/09", "ACH CREDIT Blue Harbor Supply", "", "1,875.00", "6,734.51"),
            ("07/14", "ACH PAYMENT Alder Office Services", "834.94", "", "5,899.57"),
            ("07/21", "Transfer to savings", "325.00", "", "5,574.57"),
            ("07/28", "ACH CREDIT Birch Street Market", "", "555.00", "6,129.57"),
        )):
            for x, value in zip((45, 115, 325, 400, 480), row):
                if value:
                    page.insert_text((x, 324 + index * 21), value, fontsize=10)
        return doc.tobytes(garbage=4, deflate=True)


def convert(data: bytes) -> dict:
    parsed = extract(data, MAX_PAGES, None)
    recovered_text = parsed.parser == LEGACY_OCR_PROFILE
    if parsed.status == "UNSUPPORTED":
        raise PDFError("UNSUPPORTED_LAYOUT", parsed.issues[-1].message)
    if len(parsed.transactions) > MAX_ROWS:
        raise PDFError("ROW_LIMIT", f"The demo can review up to {MAX_ROWS:,} transaction rows per statement.")
    rec = reconcile(ReconciliationInput(
        summary=parsed.summary, period_start=parsed.period_start, period_end=parsed.period_end,
        transactions=parsed.transactions,
        extraction_issues=[*parsed.issues, *(issue for row in parsed.transactions for issue in row.issues)],
    ))
    if recovered_text and rec.status != "PASSED":
        raise PDFError("UNVERIFIED_OUTLINE_TEXT", "The outlined statement could not be verified against its printed totals.")
    reviewed = reviewed_register(data)
    rows = []
    reviewed_styles = {}
    for position, row in enumerate(parsed.transactions, 1):
        check = row.category == "CLEARED_CHECK"
        transfer = row.category in {"TRANSFER_IN", "TRANSFER_OUT"}
        notes = [issue.message for issue in row.issues]
        if recovered_text:
            notes.append("Recovered from outlined statement text using classic OCR. Compare this row with the PDF.")
        if check:
            notes.append("The bank date is the clearing date. Verify the payee and obtain the written date before recording a new check.")
        if transfer:
            notes.append("Verify the counterpart account before recording this transfer.")
        action = "REVIEW" if row.issues or transfer else "CHECK_DETAILS_REQUIRED" if check else "REVIEW" if recovered_text else "IMPORT"
        name, company_split = resolve_counterparty(row.normalized_description, physical_check=check, transfer=transfer)
        approved = reviewed.get(transaction_fingerprint(row)) if reviewed else None
        if approved is not None:
            name, company_split = approved["name"], approved["split"]
            reviewed_styles[position - 1] = approved
        mapping_source = "COMPANY" if company_split is not None else ""
        if approved is not None:
            mapping_source = "BOOK11_REVIEW_TEXT" if approved.get("text_split") else "BOOK11_REVIEW"
        tax_split = georgia_its_tax_credit_split(name, row.amount if row.direction == "OUT" else None)
        if tax_split is not None:
            company_split = tax_split
            mapping_source = "GEORGIA_ITS_TAX_CREDIT"
        rows.append(ExportRow(
            id=f"TX-{position:05d}", position=position, statement_date=row.statement_date,
            qb_date=None if check else row.statement_date,
            direction=row.direction, amount=row.amount, running_balance=row.running_balance,
            original_description=row.original_description, normalized_description=row.normalized_description,
            category=row.category, check_number=row.check_number, reference=row.reference, source_page=row.source_page,
            section=row.section, action=action, qb_type="TRANSFER" if transfer else "CHECK" if check or row.direction == "OUT" else "DEPOSIT",
            qb_name=name or "", split_account=company_split, mapping_source=mapping_source,
            notes=notes, original=row.model_dump(mode="json"),
        ))
    export_input = ExportInput(
        client_name="Unassigned", bank_name=parsed.bank or "Not identified", bank_account="",
        extraction_method="LEGACY_OCR" if recovered_text else "TEXT_LAYER",
        account_last4=parsed.account_last4 or "", currency="USD-style decimals", period_start=parsed.period_start,
        period_end=parsed.period_end, fallback_account=FALLBACK, chart=None, reconciliation=rec, rows=rows,
    )
    register = statement_register(export_input)
    workbook = generate_excel(export_input, review_only=True, register=register, reviewed_styles=reviewed_styles)
    if len(workbook) > 3_900_000:
        raise PDFError("WORKBOOK_LIMIT", "The workbook exceeds the demo download limit. Use a shorter statement.")
    preview = [row.model_dump(mode="json", include={"position", "statement_date", "original_description", "direction",
                                                     "category", "amount", "source_page", "check_number", "action"})
               for row in rows[:120]]
    for row in preview:
        row["original_description"] = row["original_description"][:300]
    result = rec.model_dump(mode="json")
    result["issues"] = result["issues"][:12]
    register_preview = register.preview(row_limit=len(rows))
    register_rows = register_preview["rows"]
    register_preview["rows"] = []
    response = {
        "bank": parsed.bank, "last4": parsed.account_last4, "periodStart": parsed.period_start.isoformat() if parsed.period_start else None,
        "periodEnd": parsed.period_end.isoformat() if parsed.period_end else None, "pages": len(parsed.pages),
        "reconciliation": result, "rows": preview, "recoveredText": recovered_text, "register": register_preview,
        "workbook": base64.b64encode(workbook).decode("ascii"),
    }
    remaining = MAX_JSON_RESPONSE - len(json.dumps(json.dumps(response)))
    for row in register_rows:
        row_size = len(json.dumps(json.dumps(row))) + 2
        if row_size > remaining:
            break
        register_preview["rows"].append(row)
        remaining -= row_size
    return response


def route(method: str, path: str, headers: dict, body: bytes):
    if method == "GET" and path in ASSETS:
        filename, content_type = ASSETS[path]
        return 200, {**HEADERS, "Content-Type": content_type}, (PUBLIC / filename).read_bytes()
    if method == "GET" and path == "/sample.pdf":
        return 200, {**HEADERS, "Content-Type": "application/pdf",
                     "Content-Disposition": 'attachment; filename="bankledger-synthetic-statement.pdf"'}, sample_pdf()
    if method == "POST" and path == "/api/convert":
        if headers.get("content-type") != "application/pdf":
            return json_response(415, {"error": "Upload one PDF statement."})
        if len(body) > MAX_UPLOAD:
            return json_response(413, {"error": "Choose a PDF smaller than 4 MB."})
        try:
            return json_response(200, convert(body))
        except PDFError as exc:
            return json_response(422, {"error": str(exc), "code": exc.code})
        except Exception:
            print("BankLedger demo conversion failed")
            return json_response(500, {"error": "This PDF could not be processed. Try a fresh export from your bank."})
    return json_response(404, {"error": "Page not found."})


def json_response(status, payload):
    return status, {**HEADERS, "Content-Type": "application/json; charset=utf-8"}, json.dumps(payload).encode()


def lambda_handler(event, _context):
    headers = {key.lower(): value for key, value in (event.get("headers") or {}).items()}
    raw_body = event.get("body") or ""
    try:
        body = base64.b64decode(raw_body, validate=True) if event.get("isBase64Encoded") else raw_body.encode()
        status, response_headers, response = route(event["requestContext"]["http"]["method"], event["rawPath"], headers, body)
    except (ValueError, KeyError, binascii.Error):
        status, response_headers, response = json_response(400, {"error": "The request could not be read."})
    binary = response_headers["Content-Type"] == "application/pdf"
    return {"statusCode": status, "headers": response_headers, "isBase64Encoded": binary,
            "body": base64.b64encode(response).decode("ascii") if binary else response.decode("utf-8")}

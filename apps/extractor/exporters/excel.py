from datetime import date
from decimal import Decimal
from io import BytesIO
import json
import re

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from exporters.iif import evaluate, iif_rows, preview
from exporters.register import StatementRegister, statement_register
from schemas import ExportInput

NAVY = "14294B"
STRIPE = "F2F6FC"
RED = "FDE6E3"
GREEN = "DDF5E8"
DECIMAL_FORMAT = '#,##0.00;[Red](#,##0.00);0.00'
INVALID_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff]")

REGISTER_NAME = "Statement Register"
REGISTER_COLUMNS = {
    2: ("", 17),
    5: ("Type", 9),
    7: ("Date", 12),
    9: ("Num", 9),
    11: ("Adj", 4),
    13: ("Name", 19),
    15: ("Memo", 35),
    17: ("Clr", 4),
    19: ("Split", 25),
    21: ("Debit", 12),
    23: ("Credit", 12),
}
REGISTER_AUDIT_COLUMNS = {
    25: ("Balance", 13),
    27: ("Bank clear date", 15),
    28: ("Source page", 12),
    29: ("Review status", 23),
    30: ("Balance basis", 13),
}
REGISTER_MONEY_FORMAT = "#,##0.00;-#,##0.00"
BOOK11_MONEY_FORMAT = "#,##0.00_);(#,##0.00)"
BOOK11_SPACER_WIDTH = 2.21875
REGISTER_FONT = Font(name="Arial", size=9, color="FF323232")
REGISTER_BOLD = Font(name="Arial", size=9, color="FF323232", bold=True)
BOOK11_REVIEW_TEXT = Font(name="Arial", size=8, color="FF323232")
REGISTER_RULE = Side(style="medium", color="FF323232")


def cell_value(sheet, row, column, value):
    if isinstance(value, (list, dict, tuple)):
        value = json.dumps(value, ensure_ascii=False, default=str)
    if isinstance(value, str):
        value = INVALID_XML.sub("�", value)[:32767]
    if isinstance(value, Decimal):
        cell = sheet.cell(row, column, f"{value:.2f}")
        if len(value.normalize().as_tuple().digits) <= 15:
            # openpyxl's numeric writer otherwise formats Decimal through a binary float.
            cell.data_type = "n"
            cell.number_format = DECIMAL_FORMAT
        return cell
    cell = sheet.cell(row, column, value)
    if isinstance(value, str):
        cell.data_type = "s"
    if isinstance(value, date):
        cell.number_format = "yyyy-mm-dd"
    return cell


def register_cell(sheet, row, column, value, *, bold=False, money_format=REGISTER_MONEY_FORMAT):
    cell = cell_value(sheet, row, column, value)
    cell.font = REGISTER_BOLD if bold else REGISTER_FONT
    if isinstance(value, Decimal) and cell.data_type == "n":
        cell.number_format = money_format
    if isinstance(value, date):
        cell.number_format = "mm/dd/yyyy"
    return cell


def add_statement_register(workbook, register: StatementRegister, *, book11_layout=False, reviewed_styles=None):
    sheet = workbook.create_sheet(REGISTER_NAME)
    sheet.freeze_panes = "C2"
    sheet.sheet_view.showGridLines = False
    sheet.sheet_format.defaultRowHeight = 14.4 if book11_layout else 15
    columns = REGISTER_COLUMNS if book11_layout else REGISTER_COLUMNS | REGISTER_AUDIT_COLUMNS
    money_format = BOOK11_MONEY_FORMAT if book11_layout else REGISTER_MONEY_FORMAT
    if book11_layout:
        sheet.row_dimensions[1].height = 15
    for column in range(1, max(columns) + 1):
        heading, width = columns.get(column, ("", BOOK11_SPACER_WIDTH if book11_layout else 2.25))
        sheet.column_dimensions[get_column_letter(column)].width = width
        if heading:
            cell = register_cell(sheet, 1, column, heading, bold=True)
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = Border(bottom=REGISTER_RULE)
    explanations = (
        ("G", "Accounting date. A physical check uses its verified written date."),
        ("Q", "A check mark indicates an activity dated on the bank statement. It does not imply a QuickBooks match."),
        ("U", "Incoming funds increase the bank account as a debit."),
        ("W", "Outgoing funds decrease the bank account as a credit."),
    )
    if not book11_layout:
        explanations += (("Y", "Printed balance when available, otherwise calculated in register order."),)
    for column, detail in explanations:
        sheet[f"{column}1"].comment = Comment(detail, "BankLedger Pro")

    register_cell(sheet, 2, 2, "Beginning Balance", bold=True)
    if not book11_layout:
        register_cell(sheet, 2, 25, register.opening, bold=True)
    for index, row in enumerate(register.rows, start=3):
        reviewed_style = (reviewed_styles or {}).get(index - 3, {})
        cells = [
            (5, row.type), (7, row.date), (9, row.num), (13, row.name), (15, row.memo), (17, row.clr), (19, row.split),
            (21, row.debit), (23, row.credit),
        ]
        if not book11_layout:
            cells.extend(((25, row.balance), (27, row.bank_clear_date), (28, row.source_page),
                          (29, row.review_status), (30, row.balance_basis)))
        for column, value in cells:
            cell = register_cell(sheet, index, column, value, money_format=money_format)
            if column in (17, 28):
                cell.alignment = Alignment(horizontal="center", vertical="center")
            elif column == 15:
                cell.alignment = Alignment(wrap_text=True, vertical="center")
            elif book11_layout and column in (13, 19) and value and reviewed_style.get(
                "text_name" if column == 13 else "text_split"
            ):
                cell.font = BOOK11_REVIEW_TEXT
                cell.number_format = "@"
        memo = row.memo or ""
        if book11_layout and "height" in reviewed_style:
            if reviewed_style["height"] is not None:
                sheet.row_dimensions[index].height = reviewed_style["height"]
        elif book11_layout and 35 < len(memo) <= 42 and "\n" not in memo:
            sheet.row_dimensions[index].height = 22.8
        elif len(memo) > 42 or "\n" in memo:
            sheet.row_dimensions[index].height = min(60, 15 * max(memo.count("\n") + 1, 1 + len(memo) // 42))

    footer = len(register.rows) + 3
    if book11_layout:
        sheet.auto_filter.ref = f"E1:Y{footer}"
        sheet.column_dimensions["X"].width = BOOK11_SPACER_WIDTH
        sheet.column_dimensions.group("C", "D", outline_level=0)
        sheet.column_dimensions.group("X", "Y", outline_level=0)
    register_cell(sheet, footer, 2, "Total", bold=True)
    totals = [(21, register.debit_total), (23, register.credit_total)]
    if not book11_layout:
        totals.append((25, register.closing))
    for column, amount in totals:
        cell = register_cell(sheet, footer, column, amount, bold=True, money_format=money_format)
        cell.border = Border(top=REGISTER_RULE)


def add_sheet(workbook, name, headers, rows):
    sheet = workbook.create_sheet(name)
    sheet.freeze_panes = "C2" if name in ("BANK_TRANSACTIONS", "TRANSACTION_REVIEW") else "A2"
    sheet.sheet_view.showGridLines = False
    widths = [max(14, len(header) + 4) for header in headers]
    for column, header in enumerate(headers, 1):
        cell = cell_value(sheet, 1, column, header)
        cell.fill = PatternFill("solid", fgColor=NAVY)
        cell.font = Font(color="FFFFFF", bold=True, name="Calibri", size=11)
        cell.alignment = Alignment(vertical="center")
    sheet.row_dimensions[1].height = 28
    for row_index, row in enumerate(rows, 2):
        for column, value in enumerate(row, 1):
            cell = cell_value(sheet, row_index, column, value)
            cell.font = Font(color=NAVY, name="Calibri", size=11)
            if row_index % 2 == 0:
                cell.fill = PatternFill("solid", fgColor=STRIPE)
            if isinstance(value, str):
                widths[column - 1] = max(widths[column - 1], min(56, len(value) + 4))
    for column, width in enumerate(widths, 1):
        sheet.column_dimensions[get_column_letter(column)].width = width
    sheet.auto_filter.ref = sheet.dimensions
    return sheet


def row_for_review(row):
    return (
        row.id, row.action, row.statement_date, row.qb_date, row.direction, row.amount, row.original_description,
        row.normalized_description, row.check_number, row.qb_type, row.qb_name, row.split_account, row.memo,
        row.mapping_source, row.rule_id, row.verified, row.exported, row.duplicate_override, row.source_page,
        " | ".join(row.notes),
    )


def original_amount(original, key):
    value = original.get(key)
    return Decimal(value) if value is not None else None


def original_date(original):
    value = original.get("statement_date")
    return date.fromisoformat(value) if value else None


def generate_excel(payload: ExportInput, *, review_only: bool = False, register: StatementRegister | None = None,
                   reviewed_styles: dict | None = None) -> bytes:
    book = Workbook()
    book.remove(book.active)
    book.properties.title = "BankLedger Pro | Statement review"
    book.properties.creator = "BankLedger Pro"
    register = register if register is not None else statement_register(payload)
    add_statement_register(
        book,
        register,
        book11_layout=review_only,
        reviewed_styles=reviewed_styles if review_only else None,
    )
    candidates = evaluate(payload)
    report = preview(payload, candidates)
    if review_only:
        report = {**report, "included": 0, "excluded": 0, "fallback": sum(row.is_fallback for row in payload.rows),
                  "included_ids": [], "excluded_rows": [], "warnings": [], "blockers": []}
    rec = payload.reconciliation
    add_sheet(book, "CONTROL_PANEL", ["Field", "Value"], [
        ("Client", payload.client_name), ("Bank", payload.bank_name), ("Account ending", payload.account_last4),
        ("QuickBooks bank account", payload.bank_account or "Unconfigured"), ("Currency convention", payload.currency),
        ("Statement start", payload.period_start), ("Statement end", payload.period_end),
        ("Reading method", "Classic OCR of printed statement text. Verify recovered rows against the PDF."
         if payload.extraction_method == "LEGACY_OCR" else "Embedded PDF text"),
        ("Bank reconciliation", rec.status), ("Account-name validation", report["account_validation"]),
        ("Bank transactions", len(payload.rows)),
        ("Rows without a split" if review_only else "Rows assigned to fallback",
         sum(row.split_account is None for row in payload.rows) if review_only else report["fallback"]),
        *( [("IIF export", "Unavailable in this Excel review demo.")] if review_only else
           [("Eligible for new IIF", report["included"]), ("Excluded from new IIF", report["excluded"])] ),
        ("Purpose", "Workbook for accountant review and audit."),
        ("Physical checks", "The written date determines the accounting date. The bank date is the clear date."),
        ("Precision", "Amounts beyond Excel's 15-digit precision are stored as exact text."),
        *[("Export warning", message) for message in report["warnings"] + report["blockers"]],
    ])
    add_sheet(book, "BANK_TRANSACTIONS", [
        "ID", "Source page", "Original clearing date", "Original description", "Original bank line", "Original direction",
        "Original amount", "Original balance", "Original category", "Original check #", "Original reference", "Section",
        "Source coordinates", "Current statement date", "Current amount",
    ], [(
        row.id, row.source_page, original_date(row.original), row.original.get("original_description"),
        row.original.get("original_line"), row.original.get("direction"), original_amount(row.original, "amount"),
        original_amount(row.original, "running_balance"), row.original.get("category"), row.original.get("check_number"),
        row.original.get("reference"), row.section, row.original.get("source_bounds"), row.statement_date, row.amount,
    ) for row in payload.rows])
    review_headers = [
        "ID", "Import action", "Bank clear date", "QB date", "Direction", "Amount", "Bank description", "Review description",
        "Check #", "QB type", "QB name", "QB split account", "Memo", "Mapping source", "Rule ID", "Verified", "IIF batched",
        "Duplicate override", "Source page", "Review notes",
    ]
    if review_only:
        review_headers += ["Register running balance", "Balance basis"]
    review_entries = [
        (row, row_for_review(row) + ((register_row.balance, register_row.balance_basis) if review_only else ()))
        for row, register_row in zip(payload.rows, register.rows, strict=True)
    ]
    review = add_sheet(book, "TRANSACTION_REVIEW", review_headers, (values for _, values in review_entries))
    if payload.rows:
        for action, color in (("IMPORT", GREEN), ("REVIEW", RED), ("CHECK_DETAILS_REQUIRED", RED)):
            review.conditional_formatting.add(f"B2:B{len(payload.rows) + 1}", CellIsRule(
                operator="equal", formula=[f'"{action}"'], fill=PatternFill("solid", fgColor=color),
            ))
    add_sheet(book, "CHECK_REGISTER", [
        "Source", "ID", "Check #", "Bank clear date", "Written date", "Payee", "Amount", "QB account", "Action", "Prior match",
    ], [
        ("Statement", row.id, row.check_number, row.statement_date, row.written_date, row.qb_name, row.amount,
         row.split_account, row.action, row.prior_match)
        for row in payload.rows if row.category == "CLEARED_CHECK"
    ] + [
        ("Prior register", item["id"], item.get("number"), None, date.fromisoformat(item["date"]), item.get("name"),
         Decimal(item["amount"]), item.get("split"), item.get("status"), None) for item in payload.prior
    ])
    add_sheet(book, "NEEDS_MAPPING", review_headers, (
        values for row, values in review_entries
        if row.is_fallback or (review_only and row.split_account is None)
    ))
    rule_columns = ["scope", "direction", "category", "pattern", "method", "qbType", "qbName", "splitAccount", "memo", "priority", "active"]
    add_sheet(book, "MAPPING_RULES", rule_columns, ([rule.get(key) for key in rule_columns] for rule in payload.rules))
    add_sheet(book, "RECONCILIATION", ["Field", "Value", "Page", "Section"], [
        ("Bank status", rec.status), ("Opening balance", rec.opening), ("Credits", rec.credits), ("Debits", rec.debits),
        ("Expected ending balance", rec.expected), ("Calculated ending balance", rec.calculated), ("Difference", rec.difference),
        ("Bank row count", rec.count),
        *((issue.code, issue.message, issue.page, issue.section) for issue in rec.issues),
        *((key.replace("_", " ").title(), value) for key, value in (payload.book or {}).items()),
    ])
    included = set(report["included_ids"])
    add_sheet(book, "IIF_STAGING", ["ID", "Record", "Type", "Date", "Account", "Name", "Signed amount", "Ref #", "Memo"], [
        (candidate.row.id, line[0], line[2], candidate.row.qb_date, line[4], line[5], Decimal(line[7]), line[8], line[9])
        for candidate in candidates if candidate.row.id in included
        for line in iif_rows(candidate, payload.bank_account) if line[0] in ("TRNS", "SPL")
    ])
    add_sheet(book, "EXCEPTIONS", ["ID", "Row", "Reason"], [
        (row["id"], row["position"], reason) for row in report["excluded_rows"] for reason in row["reasons"]
    ] + [
        (row.id, row.position, reason) for row in payload.rows for reason in row.notes
    ] + [
        ("Statement", issue.page, issue.message) for issue in rec.issues
    ])
    audit_columns = ["createdAt", "actorId", "action", "subjectId", "details"]
    add_sheet(book, "AUDIT_LOG", ["Time (UTC)", "Actor ID", "Action", "Record ID", "Details"], [
        [entry.get(key) for key in audit_columns] for entry in payload.audit
    ])
    output = BytesIO()
    book.save(output)
    book.close()
    return output.getvalue()

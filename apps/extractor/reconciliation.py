from collections import defaultdict
from decimal import Decimal

from schemas import Issue, Reconciliation, ReconciliationInput


def reconcile(payload: ReconciliationInput) -> Reconciliation:
    summary = payload.summary
    unresolved = list(payload.extraction_issues)
    failures: list[Issue] = []
    totals = {"IN": Decimal(0), "OUT": Decimal(0)}
    counts = {"IN": 0, "OUT": 0}
    section_total = defaultdict(Decimal)
    section_count = defaultdict(int)
    rolling = summary.opening
    for row in payload.transactions:
        if not row.statement_date or row.direction is None or row.amount is None or row.amount <= 0:
            unresolved.append(Issue(code="MISSING_TRANSACTION_FACT", message="A bank row has an unresolved date, direction, or amount.",
                                    page=row.source_page, section=row.section))
            rolling = None
            continue
        if payload.period_start and payload.period_end and not payload.period_start <= row.statement_date <= payload.period_end:
            failures.append(Issue(code="DATE_OUTSIDE_PERIOD", message="A bank clearing date is outside the statement period.",
                                  page=row.source_page, section=row.section))
        amount = row.amount
        totals[row.direction] += amount
        counts[row.direction] += 1
        for sign in (None, row.direction):
            section_total[(row.section, sign)] += amount
            section_count[(row.section, sign)] += 1
        if rolling is not None:
            rolling += amount if row.direction == "IN" else -amount
        if row.running_balance is not None:
            if rolling is not None and rolling != row.running_balance:
                failures.append(Issue(code="RUNNING_BALANCE_MISMATCH", message=f"Printed running balance is {row.running_balance:.2f}. Expected {rolling:.2f} from the preceding balance.",
                                      page=row.source_page, section=row.section))
            rolling = row.running_balance

    opening, expected = summary.opening, summary.closing
    calculated = opening + totals["IN"] - totals["OUT"] if opening is not None else None
    difference = calculated - expected if calculated is not None and expected is not None else None
    if opening is None or expected is None:
        unresolved.append(Issue(code="MISSING_BALANCE", message="Both printed opening and closing balances are required for verification."))
    if not payload.period_start or not payload.period_end:
        unresolved.append(Issue(code="MISSING_PERIOD", message="Enter and verify the statement's start and end dates."))
    if difference is not None and difference != 0:
        failures.append(Issue(code="BALANCE_MISMATCH", message=f"Calculated closing balance differs by {difference:.2f}. Check for missing, duplicated, or mis-signed rows."))
    for direction, printed, count, label in (
        ("IN", summary.credits, summary.credit_count, "credits"),
        ("OUT", summary.debits, summary.debit_count, "debits"),
    ):
        if printed is not None and totals[direction] != printed:
            failures.append(Issue(code="PRINTED_TOTAL_MISMATCH", message=f"Printed {label} {printed:.2f} differ from extracted {totals[direction]:.2f}."))
        if count is not None and counts[direction] != count:
            failures.append(Issue(code="PRINTED_COUNT_MISMATCH", message=f"Printed {label} count {count} differs from extracted {counts[direction]}."))
    count = len(payload.transactions)
    if summary.count is not None and summary.count != count or summary.no_activity and count:
        failures.append(Issue(code="TRANSACTION_COUNT_MISMATCH", message=f"The statement's activity count does not agree with the {count} bank rows."))
    if not count and not (summary.no_activity or summary.count == 0 or summary.credits == summary.debits == 0):
        unresolved.append(Issue(code="NO_ACTIVITY_EVIDENCE", message="There is no printed evidence that the statement contains zero transactions."))
    seen = set()
    for section in summary.sections:
        key = (section.section, section.direction)
        evidence = (key, section.amount, section.count)
        if evidence in seen:
            continue
        seen.add(evidence)
        if section.amount != section_total[key] or section.count is not None and section.count != section_count[key]:
            failures.append(Issue(code="SECTION_TOTAL_MISMATCH",
                                  message=f"Printed {section.section} subtotal {section.amount:.2f} differs from the {section_total[key]:.2f} assigned to that section, or its printed count differs.",
                                  page=section.page, section=section.section))
    status = "FAILED" if failures else "UNVERIFIED" if unresolved else "PASSED"
    return Reconciliation(status=status, opening=opening, expected=expected, calculated=calculated,
                          difference=difference, credits=totals["IN"], debits=totals["OUT"],
                          count=count, issues=failures + unresolved)

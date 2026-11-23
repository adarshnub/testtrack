import { z } from "zod";

export const directions = ["IN", "OUT"] as const;
export const categories = [
  "CASH_DEPOSIT", "ACH_CREDIT", "OTHER_DEPOSIT", "ACH_PAYMENT", "CARD_PAYMENT",
  "BANK_FEE", "TRANSFER_IN", "TRANSFER_OUT", "CLEARED_CHECK", "OTHER_WITHDRAWAL", "UNKNOWN",
] as const;
export const qbTypes = ["DEPOSIT", "CHECK", "TRANSFER"] as const;
export const actions = ["IMPORT", "MATCH_EXISTING", "CHECK_DETAILS_REQUIRED", "REVIEW", "SKIP"] as const;
export const scopes = ["GLOBAL", "CLIENT", "BANK_ACCOUNT"] as const;
export const methods = ["EXACT", "CONTAINS", "STARTS_WITH"] as const;
export const date = z.iso.date();
export const id = z.uuid();
export const money = z.string().regex(/^-?\d{1,16}(\.\d{1,2})?$/, "Enter a decimal amount with up to two cents digits.");
export const positiveMoney = money.refine((value) => !value.startsWith("-") && /[1-9]/.test(value), "Enter a positive amount.");
export const text = (length: number) => z.string().max(length).refine((value) => !value.includes("\u0000"), "Remove null characters.");
export const exactName = text(240).transform((value) => value.trim())
  .pipe(z.string().min(1, "Enter the exact QuickBooks account name.").regex(/^[^\t\r\n]+$/, "Use a single line."));
export const issue = z.object({ code: z.string(), message: z.string(), page: z.number().int().nullable().optional(), section: z.string().nullable().optional() });
export const bounds = z.object({ page: z.number().int().positive(), x: z.number(), y: z.number(), width: z.number(), height: z.number() });
export const summary = z.object({
  opening: money.nullable(), closing: money.nullable(), credits: money.nullable(), debits: money.nullable(),
  count: z.number().int().nonnegative().nullable(), credit_count: z.number().int().nonnegative().nullable(),
  debit_count: z.number().int().nonnegative().nullable(), no_activity: z.boolean(),
  sections: z.array(z.object({
    section: z.string(), amount: money, direction: z.enum(directions).nullable(),
    page: z.number().int().nullable(), count: z.number().int().nonnegative().nullable(),
  })),
});
export const activity = z.object({
  statement_date: date.nullable(), direction: z.enum(directions).nullable(), amount: money.nullable(),
  running_balance: money.nullable(), section: z.string().nullable(), source_page: z.number().int().positive(),
});
export const extractedRow = activity.extend({
  original_description: z.string(), normalized_description: z.string(), category: z.enum(categories),
  check_number: z.string().nullable(), reference: z.string().nullable(), original_line: z.string(),
  source_bounds: z.array(bounds), issues: z.array(issue),
});
export const extraction = z.object({
  parser: z.string(), parser_name: z.string(), parser_version: z.number().int(),
  status: z.enum(["OK", "NEEDS_REVIEW", "UNSUPPORTED"]),
  bank: z.string().nullable(), account_last4: z.string().nullable(), period_start: date.nullable(), period_end: date.nullable(),
  summary, pages: z.array(z.object({
    number: z.number().int().positive(), rotation: z.number().int(), width: z.number().positive(), height: z.number().positive(),
    orientation: z.enum(["PORTRAIT", "LANDSCAPE"]), text: z.string(), tables: z.array(bounds),
  })),
  transactions: z.array(extractedRow), issues: z.array(issue),
});
export const reconciliationInput = z.object({
  summary, period_start: date.nullable(), period_end: date.nullable(), transactions: z.array(activity), extraction_issues: z.array(issue),
});
export const reconciliation = z.object({
  status: z.enum(["PASSED", "FAILED", "UNVERIFIED"]), opening: money.nullable(), expected: money.nullable(),
  calculated: money.nullable(), difference: money.nullable(), credits: money, debits: money, count: z.number().int(), issues: z.array(issue),
});
export const preview = z.object({
  candidates: z.number().int(), included: z.number().int(), excluded: z.number().int(), credits: money, debits: money,
  fallback: z.number().int(), included_ids: z.array(id),
  excluded_rows: z.array(z.object({ id, position: z.number().int(), reasons: z.array(z.string()) })),
  blockers: z.array(z.string()), warnings: z.array(z.string()), account_validation: z.enum(["CHECKED", "UNVERIFIED"]),
});
export const split = z.object({ account: exactName, amount: positiveMoney, name: text(240).default(""), memo: text(500).default("") });
export const ledgerRow = z.object({
  position: z.number().int().positive(), type: text(80), date, number: text(120).nullable(), name: text(240),
  memo: text(500), cleared: z.boolean().nullable(), split: text(240), amount: positiveMoney,
  direction: z.enum(directions), balance: money.nullable(), original: z.record(z.string(), z.string()),
});
export const chartAccount = z.object({ name: exactName, type: text(120).nullable() });
export const exportRow = activity.extend({
  id, position: z.number().int(), qb_date: date.nullable(), written_date: date.nullable(),
  original_description: z.string(), normalized_description: z.string(), category: z.enum(categories),
  check_number: z.string().nullable(), reference: z.string().nullable(), action: z.enum(actions), verified: z.boolean(),
  qb_type: z.enum(qbTypes), qb_name: text(240), split_account: exactName.nullable(), splits: z.array(split), memo: text(500),
  rule_id: id.nullable(), mapping_source: z.string(), is_fallback: z.boolean(), duplicate_blocked: z.boolean(),
  duplicate_override: z.string().nullable(), prior_match: id.nullable(), exported: z.boolean(),
  notes: z.array(z.string()), original: z.record(z.string(), z.unknown()),
});
export const exportInput = z.object({
  client_name: z.string(), bank_name: z.string(), bank_account: exactName, account_last4: z.string(), currency: z.string(),
  period_start: date.nullable(), period_end: date.nullable(), fallback_account: exactName,
  chart: z.array(z.string()).nullable(), counterpart_accounts: z.array(z.string()),
  compatibility: z.literal("QB_DESKTOP_US_IIF"), reconciliation, rows: z.array(exportRow),
  audit: z.array(z.unknown()), rules: z.array(z.unknown()), prior: z.array(z.unknown()), book: z.record(z.string(), z.unknown()).nullable(),
});

export type Bounds = z.infer<typeof bounds>;
export type Issue = z.infer<typeof issue>;
export type Extraction = z.infer<typeof extraction>;
export type ExtractedRow = z.infer<typeof extractedRow>;
export type Summary = z.infer<typeof summary>;
export type Reconciliation = z.infer<typeof reconciliation>;
export type ReconciliationInput = z.infer<typeof reconciliationInput>;
export type Preview = z.infer<typeof preview>;
export type ExportInput = z.infer<typeof exportInput>;
export type ExportRow = z.infer<typeof exportRow>;

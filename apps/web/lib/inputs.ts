import { z } from "zod";
import { categories, date, directions, exactName, id, methods, money, positiveMoney, qbTypes, scopes, split, summary, text } from "@bankledger/contracts";

const required = (length: number) => text(length).transform((value) => value.trim()).pipe(z.string().min(1, "This field is required."));
const optionalId = id.nullable();

export const clientInput = z.object({
  name: required(160), metadata: text(1000).default(""), active: z.boolean().default(true),
  fallbackAccount: exactName.default("Ask my Accountant"),
});
export const bankInput = z.object({
  clientId: id, bankName: required(120), displayName: required(120),
  accountLast4: z.string().regex(/^\d{4}$/, "Enter only the last four digits."),
  qbBankAccount: exactName, currency: z.string().regex(/^[A-Z]{3}$/).default("USD"),
  active: z.boolean().default(true),
});
export const ruleInput = z.object({
  scope: z.enum(scopes), clientId: optionalId, bankAccountId: optionalId, direction: z.enum(directions),
  category: z.enum(categories).nullable().default(null), pattern: required(300), method: z.enum(methods),
  qbType: z.enum(qbTypes), qbName: text(240).default(""), splitAccount: exactName, memo: text(500).default(""),
  priority: z.number().int().min(-10000).max(10000).default(0), active: z.boolean().default(true),
}).superRefine((rule, ctx) => {
  if ((rule.scope === "GLOBAL" && (rule.clientId || rule.bankAccountId)) || (rule.scope === "CLIENT" && (!rule.clientId || rule.bankAccountId))
      || (rule.scope === "BANK_ACCOUNT" && (!rule.clientId || !rule.bankAccountId))) {
    ctx.addIssue({ code: "custom", path: ["scope"], message: "Choose the client and bank account appropriate to this rule scope." });
  }
  if ((rule.qbType === "CHECK" && rule.direction !== "OUT") || (rule.qbType === "DEPOSIT" && rule.direction !== "IN")) {
    ctx.addIssue({ code: "custom", path: ["qbType"], message: "The QuickBooks type must agree with the transaction direction." });
  }
  if (rule.category === "CASH_DEPOSIT" && rule.direction !== "IN") {
    ctx.addIssue({ code: "custom", path: ["category"], message: "Cash-deposit rules apply to incoming activity." });
  }
});
export const mappingInput = z.object({
  qbType: z.enum(qbTypes), qbName: text(240), splitAccount: exactName.nullable(), memo: text(500),
  splits: z.array(split).max(20).default([]),
});
export const reviewInput = z.object({
  version: z.number().int().positive(), statementDate: date.nullable(), qbDate: date.nullable(), writtenDate: date.nullable(),
  normalizedDescription: text(3000), direction: z.enum(directions).nullable(), amount: positiveMoney.nullable(),
  checkNumber: z.string().regex(/^\d{1,20}$/).nullable(), reference: text(120).nullable(),
  runningBalance: money.nullable(), section: text(100).nullable(), category: z.enum(categories),
  decision: z.enum(["AUTO", "REVIEW", "SKIP", "MATCH_EXISTING"]), verified: z.boolean(),
  mapping: mappingInput, duplicateOverride: text(300).nullable(), note: text(500).default(""),
  saveRule: z.object({ pattern: required(300), method: z.enum(methods), scope: z.enum(["CLIENT", "BANK_ACCOUNT"]) }).nullable().default(null),
});
export const bulkInput = z.object({
  ids: z.array(id).min(1).max(150), decision: z.enum(["MAP", "REVIEW", "SKIP", "AUTO"]),
  splitAccount: exactName.nullable().default(null),
});
export const statementInput = z.object({
  revision: z.number().int().positive(), periodStart: date.nullable(), periodEnd: date.nullable(),
  summary, resolution: text(1000).nullable(), note: required(500),
}).refine((value) => !value.periodStart || !value.periodEnd || value.periodStart <= value.periodEnd, {
  message: "The statement end date must follow its start date.", path: ["periodEnd"],
});
export const manualRowInput = z.object({
  sourcePage: z.number().int().positive(),
  review: reviewInput.omit({ version: true, saveRule: true }).extend({
    statementDate: date, amount: positiveMoney, direction: z.enum(directions), note: required(500),
  }),
});
export type RuleInput = z.infer<typeof ruleInput>;
export type ReviewInput = z.infer<typeof reviewInput>;
export type BulkInput = z.infer<typeof bulkInput>;

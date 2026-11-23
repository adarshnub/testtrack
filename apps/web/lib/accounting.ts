import type { Category, Direction, ImportAction, MappingRule, QBType, ReviewDecision } from "@prisma/client";
import type { ExportRow, Issue } from "@bankledger/contracts";
import { digest } from "./security";

export const fallbackName = "Ask my Accountant";

type BaseRule = Pick<MappingRule, "id" | "scope" | "clientId" | "bankAccountId" | "direction" | "category" | "pattern" |
  "method" | "qbType" | "qbName" | "splitAccount" | "memo" | "priority" | "active">;
export type DecisionMapping = {
  source: "RULE" | "FALLBACK" | "MANUAL" | "PRIOR"; ruleId: string | null; qbType: QBType; qbName: string;
  splitAccount: string | null; memo: string; splits: ExportRow["splits"];
};

export type AccountingFacts = {
  statementDate: string | null; qbDate: string | null; writtenDate: string | null; normalizedDescription: string;
  direction: Direction | null; amount: string | null; checkNumber: string | null; reference: string | null;
  category: Category; verified: boolean; decision: ReviewDecision; factIssues: Issue[]; duplicateOverride: string | null;
};

const scopePrecedence = { GLOBAL: 0, CLIENT: 1, BANK_ACCOUNT: 2 };
const methodPrecedence = { CONTAINS: 0, STARTS_WITH: 1, EXACT: 2 };
export const normalizeDescription = (value: string) => value.trim().replace(/\s+/g, " ");
const compareText = (value: string) => normalizeDescription(value).toLowerCase();
const ranking = (rule: BaseRule) => [scopePrecedence[rule.scope], rule.priority, methodPrecedence[rule.method],
  rule.category ? 1 : 0, compareText(rule.pattern).length];

function compareRules(first: BaseRule, second: BaseRule) {
  const rank = ranking(first);
  return ranking(second).reduce((difference, value, index) => difference || value - rank[index], 0);
}

export function chooseRule(facts: Pick<AccountingFacts, "direction" | "category" | "normalizedDescription">, rules: BaseRule[], clientId: string, bankId: string) {
  const description = compareText(facts.normalizedDescription);
  const matches = rules.filter((rule) => {
    if (!rule.active || rule.direction !== facts.direction || rule.category && rule.category !== facts.category) return false;
    if (rule.scope !== "GLOBAL" && rule.clientId !== clientId || rule.scope === "BANK_ACCOUNT" && rule.bankAccountId !== bankId) return false;
    const pattern = compareText(rule.pattern);
    if (pattern === "deposit" && rule.method !== "EXACT" && !rule.category) return false;
    if (rule.method === "EXACT") return description === pattern;
    if (rule.method === "STARTS_WITH") return description.startsWith(pattern);
    return description.includes(pattern);
  }).sort(compareRules);
  const winner = matches[0];
  if (!winner) return { rule: null, conflict: false };
  const result = (rule: BaseRule) => JSON.stringify([rule.qbType, rule.qbName, rule.splitAccount, rule.memo]);
  if (matches.some((rule) => compareRules(winner, rule) === 0 && result(rule) !== result(winner))) {
    return { rule: null, conflict: true };
  }
  return { rule: winner, conflict: false };
}

export function transactionKeys(facts: AccountingFacts, originalDescription: string) {
  if (!facts.statementDate || !facts.amount || !facts.direction) return { fingerprint: null, identityKey: null };
  const amount = facts.amount;
  const direction = facts.direction;
  const description = compareText(originalDescription || facts.normalizedDescription);
  const number = facts.checkNumber?.replace(/^0+(?=\d)/, "") || "";
  const reference = compareText(facts.reference || "");
  const fingerprint = digest(JSON.stringify([facts.statementDate, amount, direction, description, number, reference]));
  const identityKey = facts.category === "CLEARED_CHECK" && number ? digest(JSON.stringify(["check", number, amount]))
    : reference ? digest(JSON.stringify(["reference", reference, direction, amount])) : fingerprint;
  return { fingerprint, identityKey };
}

export function transferIdentity(bank: string, counterpart: string | null, direction: Direction | null, date: string | null, amount: string | null) {
  if (!counterpart || !date || !direction || !amount) return null;
  const [from, to] = direction === "OUT" ? [bank, counterpart] : [counterpart, bank];
  return digest(JSON.stringify([from, to, date, amount]));
}

export type PriorEntry = {
  id: string; date: string; amount: string; number: string | null; direction: Direction; type: string;
  name: string; memo: string; split: string; cleared: boolean | null;
};

export function matchPrior(facts: AccountingFacts, entries: PriorEntry[]) {
  if (facts.category !== "CLEARED_CHECK" || !facts.checkNumber || !facts.statementDate || !facts.amount || facts.direction !== "OUT") {
    return { match: null, ambiguous: false };
  }
  const number = facts.checkNumber.replace(/^0+(?=\d)/, "");
  const candidates = entries.filter((row) =>
    row.direction === "OUT" && row.number?.replace(/^0+(?=\d)/, "") === number &&
    /^(?:check|cheque|chk|bill payment[ -]?check|bill pmt[ -]?check|billpmt[ -]?check)$/i.test(normalizeDescription(row.type)) &&
    row.date <= facts.statementDate!);
  if (candidates.length === 1 && candidates[0].amount === facts.amount) return { match: candidates[0], ambiguous: false };
  return { match: null, ambiguous: candidates.length > 0 };
}

type ResolveContext = {
  rules: BaseRule[]; clientId: string; bankId: string; fallback: string; counterpartAccounts: ReadonlySet<string>;
  prior: ReturnType<typeof matchPrior>; repeated: boolean; previousExport: boolean; confirmedCheck: boolean;
};

export function resolveAccounting(facts: AccountingFacts, context: ResolveContext, manual: DecisionMapping | null) {
  const physical = facts.category === "CLEARED_CHECK";
  const transfer = facts.category === "TRANSFER_IN" || facts.category === "TRANSFER_OUT";
  const defaultType = transfer ? "TRANSFER" : physical || facts.direction === "OUT" ? "CHECK" : "DEPOSIT";
  const matched = manual ? { rule: null, conflict: false } : chooseRule(facts, context.rules, context.clientId, context.bankId);
  const mapping: DecisionMapping = manual || (matched.rule ? {
    source: "RULE", ruleId: matched.rule.id, qbType: matched.rule.qbType, qbName: physical ? "" : matched.rule.qbName,
    splitAccount: matched.rule.splitAccount, memo: matched.rule.memo, splits: [],
  } : {
    source: "FALLBACK", ruleId: null, qbType: defaultType, qbName: "", splitAccount: transfer ? null : context.fallback, memo: "", splits: [],
  });
  const notes: string[] = facts.factIssues.map((issue) => issue.message);
  const prior = context.prior.match;
  if (prior && !manual) {
    mapping.qbName = prior.name;
    if (prior.split && !prior.split.startsWith("-")) {
      mapping.splitAccount = prior.split;
      mapping.source = "PRIOR";
      mapping.ruleId = null;
    }
  }
  if (matched.conflict) notes.push("Conflicting mapping rules have equal precedence. Choose an account or change the rule priorities.");
  if (context.repeated) notes.push("Another row in this statement has the same bank facts and reference.");
  if (context.previousExport) notes.push("The current facts overlap a saved IIF batch. Check the import history.");
  if (context.prior.ambiguous) notes.push("The register has an ambiguous check number or amount. Compare the original entry.");
  if (prior) notes.push("A unique prior register check matches the check number, amount and bank account.");
  if (context.confirmedCheck) notes.push("A previously imported IIF batch contains this check.");
  if (transfer || mapping.qbType === "TRANSFER") {
    if (mapping.qbType !== "TRANSFER" || !mapping.splitAccount || !context.counterpartAccounts.has(mapping.splitAccount)) {
      notes.push("This transfer needs an exact, verified counterpart bank account.");
    }
  }
  const hasDuplicate = context.previousExport || context.repeated;
  const confirmedCheck = context.confirmedCheck && !facts.duplicateOverride;
  const unresolvedTransfer = (transfer || mapping.qbType === "TRANSFER") &&
    (mapping.qbType !== "TRANSFER" || !mapping.splitAccount || !context.counterpartAccounts.has(mapping.splitAccount));
  const wrongType = mapping.qbType === "DEPOSIT" && facts.direction !== "IN" || mapping.qbType === "CHECK" && facts.direction !== "OUT";
  if (wrongType) notes.push("The QuickBooks type conflicts with the bank direction.");
  const factsValid = Boolean(facts.statementDate && facts.amount && facts.direction && !facts.factIssues.length);
  const checkVerified = facts.verified && facts.writtenDate && facts.statementDate && facts.writtenDate <= facts.statementDate
    && facts.checkNumber && mapping.qbType === "CHECK" && mapping.qbName.trim();
  let action: ImportAction = "IMPORT";
  if (facts.decision === "SKIP") action = "SKIP";
  else if (facts.decision === "REVIEW" || !factsValid || context.repeated && !facts.duplicateOverride || context.prior.ambiguous && !facts.duplicateOverride) action = "REVIEW";
  else if (facts.decision === "MATCH_EXISTING" || prior || confirmedCheck) action = "MATCH_EXISTING";
  else if (physical && !checkVerified) action = "CHECK_DETAILS_REQUIRED";
  else if (matched.conflict || hasDuplicate && !facts.duplicateOverride || unresolvedTransfer || wrongType || !facts.qbDate && !physical) action = "REVIEW";
  if (action === "CHECK_DETAILS_REQUIRED") notes.push("Enter and verify the physical check's written date, number and payee.");
  return {
    action, mapping, notes, priorMatchId: prior?.id ?? null, duplicateBlocked: hasDuplicate,
    qbDate: physical ? facts.writtenDate ?? prior?.date ?? null : facts.qbDate,
    writtenDate: physical ? facts.writtenDate : null,
  };
}

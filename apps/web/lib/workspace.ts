import type { z } from "zod";
import { db, logAudit, type TxDb, withOwnerLock } from "./db";
import { fail } from "./errors";
import { bankInput, clientInput, ruleInput, type RuleInput } from "./inputs";

export async function ownedClient(tx: TxDb, ownerId: string, clientId: string) {
  const client = await tx.client.findFirst({ where: { id: clientId, ownerId } });
  if (!client) fail(404, "CLIENT_NOT_FOUND", "This client was not found in your workspace.");
  return client;
}

export async function ownedBank(tx: TxDb, ownerId: string, bankAccountId: string, clientId?: string) {
  const bank = await tx.bankAccount.findFirst({
    where: { id: bankAccountId, ...(clientId ? { clientId } : {}), client: { ownerId } }, include: { client: true },
  });
  if (!bank) fail(404, "BANK_NOT_FOUND", "This bank account was not found for the selected client.");
  return bank;
}

export function listWorkspace(ownerId: string) {
  return db.client.findMany({
    where: { ownerId }, include: {
      bankAccounts: { orderBy: { displayName: "asc" }, include: {
        ledgers: { orderBy: { createdAt: "desc" }, take: 1, select: { id: true, originalName: true, asOf: true, createdAt: true, _count: { select: { rows: true } } } },
        _count: { select: { statements: true } },
      } },
      _count: { select: { accounts: { where: { active: true } } } },
    }, orderBy: { name: "asc" },
  });
}

export function saveClient(ownerId: string, values: z.infer<typeof clientInput>, clientId?: string) {
  return withOwnerLock(ownerId, async (tx) => {
    const before = clientId ? await ownedClient(tx, ownerId, clientId) : null;
    const client = before
      ? await tx.client.update({ where: { id: before.id }, data: values })
      : await tx.client.create({ data: { ...values, ownerId } });
    await logAudit(tx, { ownerId, actorId: ownerId, clientId: client.id, subjectId: client.id,
      action: before ? "CLIENT_UPDATED" : "CLIENT_CREATED", details: { before, after: client } });
    return client;
  });
}

export function saveBank(ownerId: string, values: z.infer<typeof bankInput>, bankId?: string) {
  return withOwnerLock(ownerId, async (tx) => {
    await ownedClient(tx, ownerId, values.clientId);
    const before = bankId ? await ownedBank(tx, ownerId, bankId, values.clientId) : null;
    if (before && (values.accountLast4 !== before.accountLast4 || values.currency !== before.currency) &&
      await tx.uploadedFile.count({ where: { bankAccountId: before.id } })) {
      fail(409, "ACCOUNT_IDENTITY", "Create a new bank account if the account number or currency has changed. This account has statement history.");
    }
    const bank = before
      ? await tx.bankAccount.update({ where: { id: before.id }, data: values })
      : await tx.bankAccount.create({ data: values });
    await logAudit(tx, { ownerId, actorId: ownerId, clientId: bank.clientId, subjectId: bank.id,
      action: before ? "BANK_UPDATED" : "BANK_CREATED", details: { before, after: bank } });
    return bank;
  });
}

export function ruleWhere(ownerId: string, clientId: string, bankId?: string) {
  return {
    ownerId, OR: [
      { scope: "GLOBAL" as const }, { scope: "CLIENT" as const, clientId },
      ...(bankId ? [{ scope: "BANK_ACCOUNT" as const, clientId, bankAccountId: bankId }] : []),
    ],
  };
}

export function listRules(ownerId: string) {
  return db.mappingRule.findMany({
    where: { ownerId }, orderBy: [{ active: "desc" }, { priority: "desc" }, { createdAt: "asc" }],
    include: { client: { select: { name: true } }, bankAccount: { select: { displayName: true } } },
  });
}

export async function saveRule(tx: TxDb, ownerId: string, values: RuleInput, ruleId?: string) {
  if (values.clientId) await ownedClient(tx, ownerId, values.clientId);
  if (values.bankAccountId) await ownedBank(tx, ownerId, values.bankAccountId, values.clientId ?? undefined);
  const before = ruleId ? await tx.mappingRule.findFirst({ where: { id: ruleId, ownerId } }) : null;
  if (ruleId && !before) fail(404, "RULE_NOT_FOUND", "This rule was not found in your workspace.");
  const rule = before
    ? await tx.mappingRule.update({ where: { id: before.id }, data: values })
    : await tx.mappingRule.create({ data: { ...values, ownerId } });
  await logAudit(tx, { ownerId, actorId: ownerId, clientId: rule.clientId ?? undefined, subjectId: rule.id,
    action: before ? "RULE_UPDATED" : "RULE_CREATED", details: { before, after: rule } });
  return rule;
}

export function toggleRule(ownerId: string, ruleId: string, active: boolean) {
  return withOwnerLock(ownerId, async (tx) => {
    const before = await tx.mappingRule.findFirst({ where: { id: ruleId, ownerId } });
    if (!before) fail(404, "RULE_NOT_FOUND", "This rule was not found in your workspace.");
    const rule = await tx.mappingRule.update({ where: { id: ruleId }, data: { active } });
    await logAudit(tx, { ownerId, actorId: ownerId, clientId: rule.clientId ?? undefined, subjectId: rule.id,
      action: active ? "RULE_ENABLED" : "RULE_DISABLED", details: { before: before.active, after: active } });
    return rule;
  });
}

const saintMappings = [
  ["Cash Deposit", "Undeposited Funds:Cash Deposit"],
  ["NLP CM LLC", "Gasoline Payable"], ["ALG Distribution", "Rebate Income"], ["ITG Brands", "Rebate Income"],
  ["Liggett Vector", "Rebate Income"], ["PM USA", "Rebate Income"], ["US Smokeless", "Rebate Income"], ["Helix", "Rebate Income"],
  ["ADT Security", "Security Expenses"], ["EPX FE", "Merchant Account Fees"], ["L&T Accountants", "Professional Fees"],
  ["Payroll Service", "Payroll Expenses"], ["Modisoft", "Dues and Subscriptions"], ["Lunex Telecom", "Gift Card Payable"],
  ["Waste Pro", "Waste Management Expenses"], ["Georgia ITS Tax", "Sales Tax Payable"], ["MCLANE", "Merchandise Inventory"],
  ["Frito Lay", "Merchandise Inventory"], ["Matador", "Merchandise Inventory"], ["US Express", "Money Order Payable"],
] as const;

export function seedSaintRules(ownerId: string, clientId: string) {
  return withOwnerLock(ownerId, async (tx) => {
    const client = await ownedClient(tx, ownerId, clientId);
    if (client.name.toLowerCase() !== "saint elizabeth enterprises llc") {
      fail(400, "SEED_CLIENT", "These example rules apply to Saint Elizabeth Enterprises LLC only.");
    }
    const existing = await tx.mappingRule.findMany({ where: { ownerId, clientId, scope: "CLIENT" } });
    let created = 0;
    for (const [index, [pattern, splitAccount]] of saintMappings.entries()) {
      const cash = index === 0;
      const direction = index < 8 ? "IN" : "OUT";
      if (existing.some((rule) => rule.pattern.toLowerCase() === pattern.toLowerCase() && rule.direction === direction)) continue;
      await saveRule(tx, ownerId, ruleInput.parse({
        scope: "CLIENT", clientId, bankAccountId: null, direction, pattern: cash ? "Deposit" : pattern,
        method: cash ? "EXACT" : "CONTAINS", category: cash ? "CASH_DEPOSIT" : null,
        qbType: direction === "IN" ? "DEPOSIT" : "CHECK", qbName: cash ? "" : pattern, splitAccount,
      }));
      if (cash && !existing.some((rule) => rule.pattern === "Cash Deposit" && rule.category === "CASH_DEPOSIT")) {
        await saveRule(tx, ownerId, ruleInput.parse({
          scope: "CLIENT", clientId, bankAccountId: null, direction, pattern, method: "CONTAINS",
          category: "CASH_DEPOSIT", qbType: "DEPOSIT", splitAccount,
        }));
        created++;
      }
      created++;
    }
    return { created };
  });
}

export function applicableRules(tx: TxDb, ownerId: string, clientId: string, bankId: string) {
  return tx.mappingRule.findMany({ where: { active: true, ...ruleWhere(ownerId, clientId, bankId) } });
}

export async function exportRules(ownerId: string, clientId: string | null, bankId: string | null) {
  if (clientId) await ownedClient(db, ownerId, clientId);
  if (bankId) await ownedBank(db, ownerId, bankId, clientId ?? undefined);
  const rules = await db.mappingRule.findMany({ where: clientId ? ruleWhere(ownerId, clientId, bankId ?? undefined) : { ownerId, scope: "GLOBAL" } });
  return {
    format: "bankledger-mappings-v1",
    rules: rules.map(({ scope, direction, category, pattern, method, qbType, qbName, splitAccount, memo, priority, active }) =>
      ({ scope, direction, category, pattern, method, qbType, qbName, splitAccount, memo, priority, active })),
  };
}

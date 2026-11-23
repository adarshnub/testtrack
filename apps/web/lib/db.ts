import { Prisma, PrismaClient } from "@prisma/client";

const globals = globalThis as typeof globalThis & { bankledgerDb?: PrismaClient };
export const db = globals.bankledgerDb ?? new PrismaClient();
if (process.env.NODE_ENV !== "production") globals.bankledgerDb = db;

export type TxDb = Prisma.TransactionClient;

export async function clientLock(tx: TxDb, ownerId: string) {
  await tx.$executeRaw`SELECT pg_advisory_xact_lock(hashtextextended(${ownerId}, 0))`;
}

export function withOwnerLock<T>(ownerId: string, callback: (tx: TxDb) => Promise<T>) {
  return db.$transaction(async (tx) => {
    await clientLock(tx, ownerId);
    return callback(tx);
  }, { maxWait: 6000, timeout: 125_000 });
}

export function json(value: unknown): Prisma.InputJsonValue {
  return JSON.parse(JSON.stringify(value)) as Prisma.InputJsonValue;
}

export function asMoney(value: { toFixed: (precision: number) => string } | null) {
  return value === null ? null : value.toFixed(2);
}

export type AuditFields = {
  ownerId: string; actorId?: string | null; clientId?: string; statementId?: string; action: string; subjectId?: string; details?: unknown;
};
export function logAudit(tx: TxDb, { details = {}, ...fields }: AuditFields) {
  return tx.auditLog.create({ data: { ...fields, details: json(details) }, select: { id: true } });
}

import { createHash, randomBytes, scrypt, timingSafeEqual } from "node:crypto";
import { promisify } from "node:util";
import { db } from "./db";
import { fail } from "./errors";

const derive = promisify(scrypt);
export const COOKIE_NAME = "bankledger_session";
export const SESSION_SECONDS = 12 * 60 * 60;
const fakeSalt = "ae506aec60f209f5084c96dee7ef5273f";
const invalidHash = `${fakeSalt}:${"0".repeat(128)}`;

export function digest(value: string | Uint8Array) {
  return createHash("sha256").update(value).digest("hex");
}

export async function passwordHash(password: string) {
  const salt = randomBytes(16).toString("hex");
  const hash = await derive(password, salt, 64) as Buffer;
  return `${salt}:${hash.toString("hex")}`;
}

async function passwordMatches(password: string, hash: string) {
  const [salt, encoded] = hash.split(":");
  const computed = await derive(password, salt, 64) as Buffer;
  return timingSafeEqual(computed, Buffer.from(encoded, "hex"));
}

export async function signIn(email: string, password: string) {
  const normalized = email.trim().toLowerCase();
  const key = digest(normalized);
  const cutoff = new Date(Date.now() - 15 * 60 * 1000);
  const [attempt] = await db.$queryRaw<{ attempts: number }[]>`
    INSERT INTO login_attempts (key, attempts, "updatedAt") VALUES (${key}, 1, NOW())
    ON CONFLICT (key) DO UPDATE SET attempts = CASE WHEN login_attempts."updatedAt" < ${cutoff}
      THEN 1 ELSE login_attempts.attempts + 1 END, "updatedAt" = NOW() RETURNING attempts
  `;
  if (attempt.attempts > 8) fail(429, "LOGIN_LIMIT", "Too many sign-in attempts. Wait 15 minutes before trying again.");
  const user = await db.user.findUnique({ where: { email: normalized } });
  if (!(await passwordMatches(password, user?.passwordHash ?? invalidHash)) || !user) {
    fail(401, "INVALID_LOGIN", "The email address or password is incorrect.");
  }
  const token = randomBytes(32).toString("base64url");
  await db.$transaction([
    db.loginAttempt.deleteMany({ where: { key } }),
    db.session.deleteMany({ where: { userId: user.id, expiresAt: { lte: new Date() } } }),
    db.session.create({ data: { userId: user.id, tokenHash: digest(token), expiresAt: new Date(Date.now() + SESSION_SECONDS * 1000) } }),
  ]);
  return token;
}

export async function authenticate(token: string | undefined) {
  if (!token || !/^[\w-]{43}$/.test(token)) return null;
  const session = await db.session.findUnique({
    where: { tokenHash: digest(token) }, select: { expiresAt: true, user: { select: { id: true, email: true } } },
  });
  return session && session.expiresAt > new Date() ? session.user : null;
}

export async function signOut(token: string | undefined) {
  if (token) await db.session.deleteMany({ where: { tokenHash: digest(token) } });
}

export function requireSameOrigin(request: Request) {
  const allowed = process.env.APP_ORIGIN ? new URL(process.env.APP_ORIGIN).origin : new URL(request.url).origin;
  if (request.headers.get("origin") !== allowed) fail(403, "WRONG_ORIGIN", "This request must be sent from the BankLedger workspace.");
}

export function cookieOptions() {
  return { httpOnly: true, sameSite: "lax" as const, secure: process.env.APP_ORIGIN?.startsWith("https:") ?? false, path: "/" };
}

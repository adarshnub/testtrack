import { ZodError } from "zod";

export class AppError extends Error {
  constructor(public status: number, public code: string, message: string, public context?: Record<string, unknown>) {
    super(message);
  }
}

export function fail(status: number, code: string, message: string, context?: Record<string, unknown>): never {
  throw new AppError(status, code, message, context);
}

export function responseError(error: unknown) {
  if (error instanceof AppError) {
    return Response.json({ error: error.message, code: error.code, ...error.context }, { status: error.status });
  }
  if (error instanceof ZodError) {
    const issue = error.issues[0];
    const field = issue?.path.join(" › ");
    return Response.json({ error: field ? `${field} — ${issue.message}` : (issue?.message ?? "Check the form fields."),
      code: "INVALID_INPUT" }, { status: 400 });
  }
  if (error && typeof error === "object" && "code" in error && error.code === "P2002") {
    return Response.json({ error: "A record with those settings already exists.", code: "ALREADY_EXISTS" }, { status: 409 });
  }
  console.error("BankLedger request failed", error instanceof Error ? error.name : "UnknownError");
  return Response.json({ error: "The request could not be saved. Check database and service availability, then retry.", code: "REQUEST_FAILED" }, { status: 500 });
}

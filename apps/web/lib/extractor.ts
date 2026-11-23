import { z } from "zod";
import { chartAccount, extraction, ledgerRow, preview, reconciliation, type ExportInput, type ReconciliationInput } from "@bankledger/contracts";
import { fail } from "./errors";

async function invoke(path: string, body: BodyInit, contentType: string, timeout = 80_000) {
  const token = process.env.EXTRACTOR_TOKEN;
  if (!token || token.length < 24) fail(503, "SERVICE_CONFIG", "Configure the private extraction service token.");
  let response: Response;
  try {
    response = await fetch(new URL(`/v1/${path}`, process.env.EXTRACTOR_URL || "http://127.0.0.1:8000"), {
      method: "POST", headers: { "Content-Type": contentType, Authorization: `Bearer ${token}` },
      body, signal: AbortSignal.timeout(timeout), cache: "no-store",
    });
  } catch {
    fail(503, "SERVICE_UNAVAILABLE", "The financial processing service did not respond. Check the service and retry.");
  }
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    const detail = error.detail;
    if (typeof detail?.message === "string") fail(response.status, detail.code ?? "SERVICE_ERROR", detail.message);
    fail(502, "SERVICE_ERROR", "The financial processing service could not complete the request.");
  }
  return response;
}

async function decode<S extends z.ZodType>(response: Response, schema: S): Promise<z.infer<S>> {
  const result = schema.safeParse(await response.json());
  if (!result.success) fail(502, "SERVICE_CONTRACT", "The financial processing service returned an invalid response.");
  return result.data;
}

async function processJson<S extends z.ZodType>(path: string, payload: unknown, schema: S) {
  return decode(await invoke(path, JSON.stringify(payload), "application/json"), schema);
}

export function extractPdf(file: Uint8Array, accountLast4: string) {
  return invoke(`extract?account_last4=${encodeURIComponent(accountLast4)}`, new Uint8Array(file), "application/pdf")
    .then((response) => decode(response, extraction));
}
export function verifyStatement(payload: ReconciliationInput) {
  return processJson("reconcile", payload, reconciliation);
}
export function previewExport(payload: ExportInput) {
  return processJson("preview", payload, preview);
}
export async function buildExport(type: "iif" | "excel", payload: ExportInput) {
  const response = await invoke(`exports/${type}`, JSON.stringify(payload), "application/json");
  return new Uint8Array(await response.arrayBuffer());
}
export async function importChart(file: File, extension: string) {
  const response = await invoke(`tables/chart?file_type=${encodeURIComponent(extension)}`, await file.arrayBuffer(), "application/octet-stream");
  return decode(response, z.array(chartAccount));
}
export async function importRegister(file: File, extension: string, debitSide: string) {
  const response = await invoke(`tables/register?file_type=${encodeURIComponent(extension)}&debit_side=${encodeURIComponent(debitSide)}`,
    await file.arrayBuffer(), "application/octet-stream");
  return decode(response, z.array(ledgerRow));
}

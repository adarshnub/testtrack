import { constants } from "node:fs";
import { mkdir, open, unlink } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import { basename, dirname, isAbsolute, resolve } from "node:path";
import { fail } from "./errors";

const UUID_KEY = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i;
function storageRoot() {
  const configured = process.env.FILE_STORAGE_DIR || "var/private";
  const cwd = process.cwd();
  const projectRoot = basename(cwd) === "web" && basename(dirname(cwd)) === "apps" ? resolve(cwd, "../..") : cwd;
  const absolute = isAbsolute(configured) ? configured : resolve(projectRoot, configured);
  return absolute;
}

export interface FileStorage {
  put(data: Uint8Array): Promise<string>;
  read(key: string): Promise<Uint8Array>;
  remove(key: string): Promise<void>;
}

class PrivateDisk implements FileStorage {
  private keyPath(key: string) {
    if (!UUID_KEY.test(key)) fail(500, "STORAGE_KEY", "The stored file identifier is invalid.");
    return resolve(storageRoot(), key);
  }

  async put(data: Uint8Array) {
    await mkdir(storageRoot(), { recursive: true, mode: 0o700 });
    const key = randomUUID();
    const handle = await open(this.keyPath(key), "wx", 0o600);
    try {
      await handle.writeFile(data);
      await handle.sync();
    } catch (error) {
      await handle.close();
      await this.remove(key);
      throw error;
    }
    await handle.close();
    return key;
  }

  async read(key: string) {
    const handle = await open(this.keyPath(key), constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
    try {
      return await handle.readFile();
    } finally {
      await handle.close();
    }
  }

  async remove(key: string) {
    await unlink(this.keyPath(key));
  }
}

export const storage: FileStorage = new PrivateDisk();

export function safeFilename(name: string) {
  const last = name.split(/[\\/]/).at(-1) || "upload";
  return last.replace(/[\u0000-\u001f\u007f]/g, "_").slice(0, 200);
}

export async function boundedBody(request: Request, limit: number) {
  const declared = request.headers.get("content-length");
  if (declared && Number(declared) > limit) fail(413, "FILE_SIZE", "The request exceeds the size limit.");
  const reader = request.body?.getReader();
  if (!reader) fail(400, "EMPTY_BODY", "The request is empty.");
  const parts: Uint8Array[] = [];
  let size = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > limit) {
      await reader.cancel().catch(() => undefined);
      fail(413, "FILE_SIZE", "The request exceeds the size limit.");
    }
    parts.push(value);
  }
  return Buffer.concat(parts, size);
}

export async function readForm(request: Request, limit: number) {
  const type = request.headers.get("content-type");
  if (!type?.startsWith("multipart/form-data;")) fail(415, "UPLOAD_TYPE", "Use the upload form to submit a file.");
  const buffer = await boundedBody(request, limit + 16_384);
  try {
    return await new Response(new Uint8Array(buffer), { headers: { "Content-Type": type } }).formData();
  } catch {
    fail(400, "UPLOAD_INVALID", "The upload could not be read. Select the file again.");
  }
}

export function readFile(form: FormData, limit: number) {
  const files = form.getAll("file");
  if (files.length !== 1 || !(files[0] instanceof File) || files[0].size === 0) {
    fail(400, "MISSING_FILE", "Select one nonempty file.");
  }
  if (files[0].size > limit) fail(413, "FILE_SIZE", `The maximum file size is ${Math.floor(limit / 1024 / 1024)} MB.`);
  return files[0];
}

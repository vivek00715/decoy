import * as fs from "fs";
import * as path from "path";
// fernet's `export =` (CommonJS) type declaration requires this import
// form; there is no ESM-compatible way to import it with full types.
// eslint-disable-next-line @typescript-eslint/no-require-imports
import fernet = require("fernet");

import { AuditEntry, AuditLogFile, RequestGroup, SUPPORTED_FORMAT_VERSION } from "./types";

export const DEFAULT_KEY_RELATIVE_PATH = path.join(".decoy", "vault.key");
export const DEFAULT_AUDIT_LOG_RELATIVE_PATH = path.join(".decoy", "audit.enc");

export class AuditLogReadError extends Error {}

/**
 * Load the local encryption key the same way decoy.crypto.load_or_create_key
 * does on the Python side: DECOY_ENCRYPTION_KEY env var first, else the key
 * file at .decoy/vault.key. Unlike the Python side, this reader never
 * GENERATES a key -- if neither is present, there is nothing to read yet
 * (no audit log has been written), which is reported distinctly from a
 * decryption failure.
 */
export function loadKey(
  rootDir: string,
  env: NodeJS.ProcessEnv = process.env,
): string | null {
  const envKey = env.DECOY_ENCRYPTION_KEY;
  if (envKey) {
    return envKey;
  }
  const keyPath = path.join(rootDir, DEFAULT_KEY_RELATIVE_PATH);
  if (!fs.existsSync(keyPath)) {
    return null;
  }
  return fs.readFileSync(keyPath, "utf8").trim();
}

/**
 * Decrypt and parse the audit log at .decoy/audit.enc under `rootDir`.
 * Fails safe: any missing file, missing key, decryption failure, or
 * unsupported format_version returns an EMPTY log (never throws into the
 * caller, never guesses at a shape it doesn't recognize) -- consistent
 * with the Python side's AuditLog._load(), which does the same.
 */
export function readAuditLog(rootDir: string, env: NodeJS.ProcessEnv = process.env): AuditLogFile {
  const empty: AuditLogFile = { format_version: SUPPORTED_FORMAT_VERSION, entries: [] };

  const logPath = path.join(rootDir, DEFAULT_AUDIT_LOG_RELATIVE_PATH);
  if (!fs.existsSync(logPath)) {
    return empty;
  }

  const key = loadKey(rootDir, env);
  if (!key) {
    return empty;
  }

  try {
    const tokenText = fs.readFileSync(logPath, "utf8");
    const secret = new fernet.Secret(key);
    const token = new fernet.Token({ secret, token: tokenText, ttl: 0 });
    const decoded = token.decode();
    const parsed = JSON.parse(decoded) as AuditLogFile;

    if (parsed.format_version !== SUPPORTED_FORMAT_VERSION) {
      // Deliberate compatibility gate -- see AUDIT_LOG_FORMAT.md's
      // "Versioning / migration story". Never guess at an unrecognized shape.
      return empty;
    }
    return parsed;
  } catch {
    // Corrupt file, wrong key, malformed JSON -- fail to an empty log
    // rather than crashing the extension or showing garbage.
    return empty;
  }
}

/**
 * Encrypt and atomically write an AuditLogFile back to .decoy/audit.enc.
 * No-op (does nothing, returns false) if there is no key available --
 * clearing a log that doesn't exist yet is not an error.
 */
export function writeAuditLog(
  rootDir: string,
  data: AuditLogFile,
  env: NodeJS.ProcessEnv = process.env,
): boolean {
  const key = loadKey(rootDir, env);
  if (!key) {
    return false;
  }
  const decoyDir = path.join(rootDir, ".decoy");
  fs.mkdirSync(decoyDir, { recursive: true });
  const secret = new fernet.Secret(key);
  const token = new fernet.Token({ secret });
  const encoded = token.encode(JSON.stringify(data));

  const finalPath = path.join(rootDir, DEFAULT_AUDIT_LOG_RELATIVE_PATH);
  const tmpPath = finalPath + ".tmp";
  fs.writeFileSync(tmpPath, encoded, "utf8");
  fs.renameSync(tmpPath, finalPath);
  return true;
}

/** Remove all entries for one session_id. Returns the number removed. */
export function clearAuditSession(
  rootDir: string,
  sessionId: string,
  env: NodeJS.ProcessEnv = process.env,
): number {
  const current = readAuditLog(rootDir, env);
  const before = current.entries.length;
  const filtered = current.entries.filter((e) => e.session_id !== sessionId);
  writeAuditLog(rootDir, { format_version: current.format_version, entries: filtered }, env);
  return before - filtered.length;
}

/** Remove every entry. Returns the number removed. */
export function clearAuditAll(rootDir: string, env: NodeJS.ProcessEnv = process.env): number {
  const current = readAuditLog(rootDir, env);
  const before = current.entries.length;
  writeAuditLog(rootDir, { format_version: current.format_version, entries: [] }, env);
  return before;
}

/** Group flat entries by request_id, newest request first. */
export function groupByRequest(entries: AuditEntry[]): RequestGroup[] {
  const byId = new Map<string, AuditEntry[]>();
  for (const entry of entries) {
    const list = byId.get(entry.request_id);
    if (list) {
      list.push(entry);
    } else {
      byId.set(entry.request_id, [entry]);
    }
  }

  const groups: RequestGroup[] = [];
  for (const [requestId, groupEntries] of byId) {
    groups.push({
      request_id: requestId,
      session_id: groupEntries[0].session_id,
      timestamp: groupEntries[0].timestamp,
      entries: groupEntries,
    });
  }

  groups.sort((a, b) => (a.timestamp < b.timestamp ? 1 : a.timestamp > b.timestamp ? -1 : 0));
  return groups;
}

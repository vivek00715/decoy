import * as fs from "fs";
import * as path from "path";
// eslint-disable-next-line @typescript-eslint/no-require-imports
import fernet = require("fernet");

import { loadKey } from "./auditLog";

export const DEFAULT_VAULT_RELATIVE_PATH = path.join(".decoy", "vault.enc");

// The vault's decrypted shape: session_id -> { original: fake }. This
// extension never reads original/fake VALUES for display -- it only
// needs the ability to remove a whole session's entry (or all of them)
// when the user clears data from the UI, mirroring
// decoy.vault.VaultManager.clear_session/clear_all on the Python side.
type VaultData = Record<string, Record<string, string>>;

function readVaultRaw(rootDir: string, env: NodeJS.ProcessEnv = process.env): VaultData {
  const vaultPath = path.join(rootDir, DEFAULT_VAULT_RELATIVE_PATH);
  if (!fs.existsSync(vaultPath)) {
    return {};
  }
  const key = loadKey(rootDir, env);
  if (!key) {
    return {};
  }
  try {
    const tokenText = fs.readFileSync(vaultPath, "utf8");
    const secret = new fernet.Secret(key);
    const token = new fernet.Token({ secret, token: tokenText, ttl: 0 });
    const decoded = token.decode();
    return JSON.parse(decoded) as VaultData;
  } catch {
    return {};
  }
}

function writeVaultRaw(rootDir: string, data: VaultData, env: NodeJS.ProcessEnv = process.env): boolean {
  const key = loadKey(rootDir, env);
  if (!key) {
    return false;
  }
  const decoyDir = path.join(rootDir, ".decoy");
  fs.mkdirSync(decoyDir, { recursive: true });
  const secret = new fernet.Secret(key);
  const token = new fernet.Token({ secret });
  const encoded = token.encode(JSON.stringify(data));

  const finalPath = path.join(rootDir, DEFAULT_VAULT_RELATIVE_PATH);
  const tmpPath = finalPath + ".tmp";
  fs.writeFileSync(tmpPath, encoded, "utf8");
  fs.renameSync(tmpPath, finalPath);
  return true;
}

/** Remove one session's original<->fake mappings from the vault, if a
 * persisted vault file exists at all (in-memory-only vaults have nothing
 * on disk for this extension to touch). */
export function clearVaultSession(
  rootDir: string,
  sessionId: string,
  env: NodeJS.ProcessEnv = process.env,
): void {
  const vaultPath = path.join(rootDir, DEFAULT_VAULT_RELATIVE_PATH);
  if (!fs.existsSync(vaultPath)) {
    return;
  }
  const data = readVaultRaw(rootDir, env);
  delete data[sessionId];
  writeVaultRaw(rootDir, data, env);
}

/** Remove every session's mappings from the vault, if a persisted vault
 * file exists. Writes back an empty object rather than deleting the
 * file, matching VaultManager.clear_all()'s Python-side behavior. */
export function clearVaultAll(rootDir: string, env: NodeJS.ProcessEnv = process.env): void {
  const vaultPath = path.join(rootDir, DEFAULT_VAULT_RELATIVE_PATH);
  if (!fs.existsSync(vaultPath)) {
    return;
  }
  writeVaultRaw(rootDir, {}, env);
}

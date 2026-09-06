/**
 * Pure logic for writing/merging into `.claude/settings.json`'s `env`
 * block -- confirmed directly against a real `claude` CLI install (not
 * assumed from documentation): a project-level `.claude/settings.json`
 * containing `{"env": {"ANTHROPIC_BASE_URL": "..."}}` is read and
 * actually applied at launch, with NO shell export required. Verified
 * by pointing it at a local HTTP listener with the real env var
 * explicitly unset (`env -u ANTHROPIC_BASE_URL claude -p ...`) and
 * observing the request land on that listener anyway.
 *
 * This replaces the manual "export ANTHROPIC_BASE_URL=... in a shell
 * Claude Code is then launched from" step -- the extension writes this
 * file the same way mcpConfig.ts already writes `.mcp.json`, so pointing
 * Claude Code at the chat proxy needs zero manual environment setup.
 *
 * Kept host-free (no `vscode` import), same reasoning as mcpConfig.ts:
 * the merge logic is the riskiest part (this file may have unrelated
 * hand-set settings already) and is unit-testable as a plain function.
 */

export interface ClaudeSettingsFile {
  env?: Record<string, string>;
  [key: string]: unknown;
}

export function emptyClaudeSettings(): ClaudeSettingsFile {
  return {};
}

/** Parse an existing `.claude/settings.json`. Missing/malformed content
 * fails safe to empty settings, matching mcpConfig.ts's
 * parseMcpConfig -- callers decide whether a parse failure should block
 * the write (see buildClaudeSettingsUpdate, which surfaces this as an
 * error rather than clobbering a file it can't understand). */
export function parseClaudeSettings(text: string | undefined): ClaudeSettingsFile {
  if (!text || text.trim().length === 0) {
    return emptyClaudeSettings();
  }
  const parsed: unknown = JSON.parse(text);
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    throw new Error(".claude/settings.json does not contain a JSON object");
  }
  const obj = parsed as Record<string, unknown>;
  const env = typeof obj.env === "object" && obj.env !== null ? (obj.env as Record<string, string>) : {};
  return { ...obj, env };
}

export interface ClaudeSettingsUpdateResult {
  updated: ClaudeSettingsFile;
  /** True if an existing key under `env` with the same name is being
   * overwritten with a DIFFERENT value (the caller should mention this
   * in its confirmation UI, same as mcpConfig.ts's overwritingExisting). */
  overwritingExisting: boolean;
}

/**
 * Merge ONE env var into `existing.env` WITHOUT touching any other key
 * already set there (a user's own `ANTHROPIC_MODEL`, `DISABLE_TELEMETRY`,
 * etc. must survive untouched) or any other top-level settings key
 * (`enabledPlugins`, `theme`, ...).
 */
export function buildClaudeSettingsUpdate(
  existing: ClaudeSettingsFile,
  envKey: string,
  envValue: string,
): ClaudeSettingsUpdateResult {
  const currentEnv = existing.env ?? {};
  const overwritingExisting = envKey in currentEnv && currentEnv[envKey] !== envValue;
  const updated: ClaudeSettingsFile = {
    ...existing,
    env: { ...currentEnv, [envKey]: envValue },
  };
  return { updated, overwritingExisting };
}

/** Removes ONE env var from `existing.env`, leaving everything else
 * untouched -- used by the "Stop chat proxy" / "unwire" path so turning
 * the proxy off also stops Claude Code from pointing at a dead port. */
export function removeClaudeSettingsEnvKey(existing: ClaudeSettingsFile, envKey: string): ClaudeSettingsFile {
  if (!existing.env || !(envKey in existing.env)) {
    return existing;
  }
  const rest = Object.fromEntries(Object.entries(existing.env).filter(([key]) => key !== envKey));
  return { ...existing, env: rest };
}

export function serializeClaudeSettings(settings: ClaudeSettingsFile): string {
  return JSON.stringify(settings, null, 2) + "\n";
}

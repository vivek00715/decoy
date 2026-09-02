import * as fs from "fs";
import * as path from "path";

import { emptyOverridesFile, OverridesFile } from "./types";

export const DEFAULT_OVERRIDES_RELATIVE_PATH = path.join(".decoy", "overrides.json");

/**
 * Read .decoy/overrides.json. Missing or malformed files fail safe to an
 * empty OverridesFile (matching decoy.overrides.OverrideStore's Python-side
 * behavior: an unreadable overrides file means no manual overrides are
 * active, not a crash).
 */
export function readOverrides(rootDir: string): OverridesFile {
  const overridesPath = path.join(rootDir, DEFAULT_OVERRIDES_RELATIVE_PATH);
  if (!fs.existsSync(overridesPath)) {
    return emptyOverridesFile();
  }
  try {
    const raw = fs.readFileSync(overridesPath, "utf8");
    const parsed = JSON.parse(raw);
    return normalizeOverrides(parsed);
  } catch {
    return emptyOverridesFile();
  }
}

function normalizeOverrides(parsed: unknown): OverridesFile {
  const result = emptyOverridesFile();
  if (typeof parsed !== "object" || parsed === null) {
    return result;
  }
  const obj = parsed as Record<string, unknown>;
  for (const section of ["always_mask", "never_mask"] as const) {
    const sectionValue = obj[section];
    if (typeof sectionValue !== "object" || sectionValue === null) {
      continue;
    }
    const sectionObj = sectionValue as Record<string, unknown>;
    if (Array.isArray(sectionObj.patterns)) {
      result[section].patterns = sectionObj.patterns.filter((p): p is string => typeof p === "string");
    }
    if (Array.isArray(sectionObj.field_names)) {
      result[section].field_names = sectionObj.field_names.filter(
        (f): f is string => typeof f === "string",
      );
    }
  }
  return result;
}

/**
 * Write .decoy/overrides.json atomically (write-then-rename), the same
 * pattern the Python side uses for its own encrypted stores, so a reader
 * (this extension's own file watcher, or the Python OverrideStore's
 * mtime-based reload) never sees a half-written file.
 */
export function writeOverrides(rootDir: string, overrides: OverridesFile): void {
  const decoyDir = path.join(rootDir, ".decoy");
  fs.mkdirSync(decoyDir, { recursive: true });
  const finalPath = path.join(decoyDir, "overrides.json");
  const tmpPath = finalPath + ".tmp";
  fs.writeFileSync(tmpPath, JSON.stringify(overrides, null, 2), "utf8");
  fs.renameSync(tmpPath, finalPath);
}

/**
 * Reset .decoy/overrides.json to empty. Used by the "Clear All Local
 * Data" action -- unlike clearing the audit log/vault, this is opt-in
 * destructive on the user's own configured rules, so callers must only
 * invoke this after explicit confirmation (see panelController.ts).
 * No-op if no overrides file exists yet.
 */
export function clearOverrides(rootDir: string): void {
  const overridesPath = path.join(rootDir, DEFAULT_OVERRIDES_RELATIVE_PATH);
  if (!fs.existsSync(overridesPath)) {
    return;
  }
  writeOverrides(rootDir, emptyOverridesFile());
}

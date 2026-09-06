/**
 * Stores the Anthropic API key using VS Code's SecretStorage API (backed
 * by the OS-native credential store -- Keychain on macOS, Credential
 * Manager on Windows, libsecret on Linux), not a manually-exported shell
 * variable or a plaintext file. Manual step this replaces: before this,
 * getting a masked request through required opening a terminal and
 * `export ANTHROPIC_API_KEY=...` by hand, every session.
 *
 * Kept host-free (no `vscode` import), same pattern as panelController.ts/
 * mcpConfigCommand.ts: the actual `vscode.SecretStorage` calls only exist
 * in extension.ts, which cannot be exercised without a real VS Code host
 * -- everything else (the prompt-then-store flow, the "already set"
 * check) is a plain function testable against an in-memory fake.
 */

export const SECRET_KEY_ANTHROPIC_API_KEY = "decoy.anthropicApiKey";

export interface SecretsHost {
  getSecret(key: string): Promise<string | undefined>;
  storeSecret(key: string, value: string): Promise<void>;
  deleteSecret(key: string): Promise<void>;
  /** Returns the entered value, or undefined if the user cancelled. */
  promptForApiKey(promptText: string): Promise<string | undefined>;
  showInfo(message: string): void;
  showError(message: string): void;
}

const PROMPT_TEXT =
  "Enter your Anthropic API key (from console.anthropic.com) -- stored in your OS's secure " +
  "credential store, never written to a file. This is a separate, billed API key: a Claude " +
  "Pro/Max subscription's login does not work here (different credential system, different " +
  "billing) -- see README.md's chat-proxy section.";

/** Runs the "Set API Key" command: prompt, validate non-empty, store.
 * Returns true if a key was actually stored (false on cancel/blank). */
export async function runSetApiKey(host: SecretsHost): Promise<boolean> {
  const value = await host.promptForApiKey(PROMPT_TEXT);
  if (!value || value.trim().length === 0) {
    return false;
  }
  await host.storeSecret(SECRET_KEY_ANTHROPIC_API_KEY, value.trim());
  host.showInfo("Decoy: Anthropic API key stored securely. The chat proxy can now be started.");
  return true;
}

export async function clearApiKey(host: SecretsHost): Promise<void> {
  await host.deleteSecret(SECRET_KEY_ANTHROPIC_API_KEY);
  host.showInfo("Decoy: stored Anthropic API key removed.");
}

/**
 * Used right before starting the chat proxy: returns the stored key, or
 * prompts for one on the spot if none is set yet (so "Start Proxy" works
 * as a single action for a first-time user instead of requiring a
 * separate "Set API Key" step first). Returns undefined only if the user
 * cancels the prompt.
 */
export async function ensureApiKey(host: SecretsHost): Promise<string | undefined> {
  const existing = await host.getSecret(SECRET_KEY_ANTHROPIC_API_KEY);
  if (existing) {
    return existing;
  }
  const stored = await runSetApiKey(host);
  if (!stored) {
    return undefined;
  }
  return host.getSecret(SECRET_KEY_ANTHROPIC_API_KEY);
}

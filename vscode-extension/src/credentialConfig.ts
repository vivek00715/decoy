/**
 * Credential-MODE layer on top of apiKeyCommand.ts: "Set API Key" is now
 * a choice between two ways of reaching an LLM API, not just a single
 * key prompt.
 *
 *   - Direct (API Key): unchanged behavior from apiKeyCommand.ts --
 *     ANTHROPIC_API_KEY sent as `x-api-key` straight to api.anthropic.com.
 *   - Org Gateway (Auth Token): routes through an internal AI gateway
 *     instead, authenticated via `Authorization: Bearer <token>` (see
 *     chat_proxy.py's ANTHROPIC_AUTH_TOKEN support) -- what an org that
 *     fronts Claude Code with its own gateway (TLS-inspecting corporate
 *     proxy, audit logging, cost controls, etc.) actually needs.
 *
 * Same host-free testable pattern as apiKeyCommand.ts: this module only
 * calls the SecretsHost interface, never `vscode` directly -- extension.ts
 * wires the real prompts/SecretStorage; tests use an in-memory fake.
 *
 * Stored via the SAME secure-storage mechanism as the API key (SecretsHost
 * -> vscode.SecretStorage / PasswordSafe on the IntelliJ side) -- the
 * gateway token and base URL are both credentials-adjacent (the URL
 * itself can reveal an org's internal network topology) and neither
 * belongs in a plaintext settings file.
 */

import { runSetApiKey, SECRET_KEY_ANTHROPIC_API_KEY, SecretsHost } from "./apiKeyCommand";

export type CredentialMode = "direct" | "gateway";

export const SECRET_KEY_CREDENTIAL_MODE = "decoy.credentialMode";
export const SECRET_KEY_GATEWAY_AUTH_TOKEN = "decoy.gatewayAuthToken";
export const SECRET_KEY_GATEWAY_BASE_URL = "decoy.gatewayBaseUrl";
export const SECRET_KEY_GATEWAY_SKIP_TLS_VERIFY = "decoy.gatewaySkipTlsVerify";

export const MODE_LABEL_DIRECT = "Direct (API Key)";
export const MODE_LABEL_GATEWAY = "Org Gateway (Auth Token)";

export const GATEWAY_TOKEN_PROMPT =
  "Enter your org gateway auth token (sent as \"Authorization: Bearer <token>\") -- stored in " +
  "your OS's secure credential store, never written to a file.";

export const GATEWAY_URL_PROMPT =
  "Enter your org's AI gateway base URL (e.g. https://api-eu1.aigateway.example.com) -- the " +
  "chat proxy will send requests here instead of api.anthropic.com directly.";

export const TLS_SKIP_WARNING =
  "This disables certificate verification for outbound requests to your gateway -- only enable " +
  "this if your organization's network requires it (e.g. a TLS-inspecting corporate proxy). " +
  "Leaving this OFF is correct and safe for most setups.";

export interface DirectCredentials {
  mode: "direct";
  apiKey: string;
}

export interface GatewayCredentials {
  mode: "gateway";
  authToken: string;
  baseUrl: string;
  skipTlsVerify: boolean;
}

export type ResolvedCredentials = DirectCredentials | GatewayCredentials;

/** Runs the "Set Credentials" flow: choose a mode, then prompt for that
 * mode's fields. Returns true only if credentials were actually stored
 * (false on cancel at any step). Never mutates anything on cancel. */
export async function runSetCredentials(host: SecretsHost): Promise<boolean> {
  const choice = await host.promptForChoice("How does Decoy's chat proxy reach the LLM API?", [
    MODE_LABEL_DIRECT,
    MODE_LABEL_GATEWAY,
  ]);
  if (!choice) {
    return false;
  }

  if (choice === MODE_LABEL_DIRECT) {
    const stored = await runSetApiKey(host);
    if (stored) {
      await host.storeSecret(SECRET_KEY_CREDENTIAL_MODE, "direct");
    }
    return stored;
  }

  const token = await host.promptForText(GATEWAY_TOKEN_PROMPT, { password: true });
  if (!token || token.trim().length === 0) {
    return false;
  }

  const baseUrl = await host.promptForText(GATEWAY_URL_PROMPT);
  if (!baseUrl || baseUrl.trim().length === 0) {
    return false;
  }

  // Defaults to OFF and requires an explicit, separately-worded
  // confirmation -- never inferred from the URL prompt or asked as a
  // throwaway yes/no alongside it.
  const skipTlsVerify = await host.confirmDangerousToggle(
    `Skip TLS certificate verification for requests to ${baseUrl.trim()}?\n\n${TLS_SKIP_WARNING}`,
  );

  await host.storeSecret(SECRET_KEY_GATEWAY_AUTH_TOKEN, token.trim());
  await host.storeSecret(SECRET_KEY_GATEWAY_BASE_URL, baseUrl.trim());
  await host.storeSecret(SECRET_KEY_GATEWAY_SKIP_TLS_VERIFY, skipTlsVerify ? "true" : "false");
  await host.storeSecret(SECRET_KEY_CREDENTIAL_MODE, "gateway");
  host.showInfo(
    `Decoy: org gateway credentials stored securely (${baseUrl.trim()}).` +
      (skipTlsVerify ? " TLS verification is DISABLED for this gateway." : ""),
  );
  return true;
}

export async function clearCredentials(host: SecretsHost): Promise<void> {
  await host.deleteSecret(SECRET_KEY_ANTHROPIC_API_KEY);
  await host.deleteSecret(SECRET_KEY_GATEWAY_AUTH_TOKEN);
  await host.deleteSecret(SECRET_KEY_GATEWAY_BASE_URL);
  await host.deleteSecret(SECRET_KEY_GATEWAY_SKIP_TLS_VERIFY);
  await host.deleteSecret(SECRET_KEY_CREDENTIAL_MODE);
  host.showInfo("Decoy: stored credentials removed.");
}

/**
 * Used right before starting the chat proxy: resolves whichever
 * credentials are already stored (mode-aware), or runs the full "Set
 * Credentials" flow on the spot if none exist yet -- same "Start just
 * works" first-run behavior as apiKeyCommand.ts's ensureApiKey, extended
 * to cover both modes.
 *
 * If SECRET_KEY_CREDENTIAL_MODE says "gateway" but the token/URL are
 * missing (partially cleared, corrupted storage), this falls through to
 * re-prompting rather than silently resolving to direct mode -- a stale
 * mode flag must never cause requests to silently go straight to
 * Anthropic instead of the org's approved gateway.
 */
export async function ensureCredentials(host: SecretsHost): Promise<ResolvedCredentials | undefined> {
  const mode = (await host.getSecret(SECRET_KEY_CREDENTIAL_MODE)) ?? "direct";

  if (mode === "gateway") {
    const authToken = await host.getSecret(SECRET_KEY_GATEWAY_AUTH_TOKEN);
    const baseUrl = await host.getSecret(SECRET_KEY_GATEWAY_BASE_URL);
    if (authToken && baseUrl) {
      const skipTlsVerify = (await host.getSecret(SECRET_KEY_GATEWAY_SKIP_TLS_VERIFY)) === "true";
      return { mode: "gateway", authToken, baseUrl, skipTlsVerify };
    }
  } else {
    const apiKey = await host.getSecret(SECRET_KEY_ANTHROPIC_API_KEY);
    if (apiKey) {
      return { mode: "direct", apiKey };
    }
  }

  const stored = await runSetCredentials(host);
  if (!stored) {
    return undefined;
  }
  return ensureCredentials(host);
}

/** Maps resolved credentials onto the env vars chat_proxy.py actually
 * reads (ChatProxyConfig.from_env(): ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN
 * + DECOY_ANTHROPIC_UPSTREAM_URL + DECOY_UPSTREAM_SKIP_TLS_VERIFY) -- kept
 * as its own function so a mismatch between what this extension sends and
 * what the proxy backend expects is a one-function diff, not a hunt
 * through extension.ts. */
export function buildProxyEnv(creds: ResolvedCredentials, baseEnv: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
  if (creds.mode === "direct") {
    return { ...baseEnv, ANTHROPIC_API_KEY: creds.apiKey } as NodeJS.ProcessEnv;
  }
  return {
    ...baseEnv,
    ANTHROPIC_AUTH_TOKEN: creds.authToken,
    DECOY_ANTHROPIC_UPSTREAM_URL: creds.baseUrl,
    DECOY_UPSTREAM_SKIP_TLS_VERIFY: creds.skipTlsVerify ? "true" : "false",
  } as NodeJS.ProcessEnv;
}

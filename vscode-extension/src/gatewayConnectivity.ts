/**
 * Item 3's "what does the user see if the gateway is unreachable or the
 * token is rejected" -- answered by actually probing the gateway with a
 * minimal real request BEFORE the chat proxy is started (or right after
 * credentials are saved), rather than waiting for a real user prompt to
 * fail silently inside Claude Code with no visible cause.
 *
 * This matters specifically for gateway mode: the chat proxy is a local
 * HTTP server that starts up fine (process-level "running") even when the
 * upstream gateway rejects every request -- an unreachable/401 gateway is
 * invisible to ChatProxyManager's process-lifecycle state, which only
 * tracks whether the LOCAL process is alive. This module is the piece
 * that actually calls the gateway and classifies what came back.
 *
 * `requestFn` is injected (same DI pattern as chatProxyManager.ts's
 * SpawnFn) so this is testable against a fake HTTP call, never a real
 * network request in tests -- nodeGatewayRequest below is the one real
 * implementation, wired in by extension.ts.
 */

import { request as httpRequest } from "http";
import { request as httpsRequest } from "https";

export type GatewayCheckResult =
  | { ok: true }
  | { ok: false; kind: "unauthorized" | "unreachable" | "http_error"; message: string };

export interface GatewayCheckCredentials {
  authToken: string;
  baseUrl: string;
  skipTlsVerify: boolean;
}

export type GatewayRequestFn = (
  url: string,
  headers: Record<string, string>,
  body: string,
  skipTlsVerify: boolean,
) => Promise<{ status: number; body: string }>;

/** Sends the same minimal shape chat_proxy.py's messages() handler
 * forwards upstream, so a pass here means a real end-to-end auth
 * round-trip succeeded -- not just "the URL resolves". */
export async function checkGatewayConnectivity(
  creds: GatewayCheckCredentials,
  requestFn: GatewayRequestFn,
): Promise<GatewayCheckResult> {
  const url = `${creds.baseUrl.replace(/\/+$/, "")}/v1/messages`;
  const body = JSON.stringify({
    model: "claude-haiku-4-5",
    max_tokens: 1,
    messages: [{ role: "user", content: "ping" }],
  });

  let response: { status: number; body: string };
  try {
    response = await requestFn(
      url,
      {
        authorization: `Bearer ${creds.authToken}`,
        "content-type": "application/json",
        "anthropic-version": "2023-06-01",
      },
      body,
      creds.skipTlsVerify,
    );
  } catch (err) {
    return {
      ok: false,
      kind: "unreachable",
      message:
        `Decoy: could not reach ${creds.baseUrl} (${err instanceof Error ? err.message : String(err)}). ` +
        "Check the gateway URL, your network connection, and whether TLS verification needs to be " +
        "skipped for this network (Set Credentials -> Org Gateway).",
    };
  }

  if (response.status === 401 || response.status === 403) {
    return {
      ok: false,
      kind: "unauthorized",
      message: `Decoy: gateway rejected the auth token (HTTP ${response.status}). Re-run "Set Credentials" with a valid token.`,
    };
  }
  if (response.status >= 400) {
    return {
      ok: false,
      kind: "http_error",
      message: `Decoy: gateway returned HTTP ${response.status}: ${response.body.slice(0, 200)}`,
    };
  }
  return { ok: true };
}

/** Real implementation using Node's built-in https/http -- no new
 * dependency needed. Not used by any test (tests inject a fake
 * GatewayRequestFn instead); wired in by extension.ts only. */
export function nodeGatewayRequest(
  url: string,
  headers: Record<string, string>,
  body: string,
  skipTlsVerify: boolean,
): Promise<{ status: number; body: string }> {
  const parsed = new URL(url);
  const requestFn = parsed.protocol === "https:" ? httpsRequest : httpRequest;
  return new Promise((resolve, reject) => {
    const req = requestFn(
      {
        hostname: parsed.hostname,
        port: parsed.port || (parsed.protocol === "https:" ? 443 : 80),
        path: parsed.pathname + parsed.search,
        method: "POST",
        headers: { ...headers, "content-length": Buffer.byteLength(body) },
        // Opt-in only, matches DECOY_UPSTREAM_SKIP_TLS_VERIFY's contract
        // on the chat_proxy.py side -- never on by default.
        rejectUnauthorized: !skipTlsVerify,
        timeout: 10_000,
      },
      (res) => {
        const chunks: Buffer[] = [];
        res.on("data", (chunk) => chunks.push(chunk));
        res.on("end", () => resolve({ status: res.statusCode ?? 0, body: Buffer.concat(chunks).toString("utf8") }));
      },
    );
    req.on("timeout", () => req.destroy(new Error("request timed out")));
    req.on("error", reject);
    req.write(body);
    req.end();
  });
}

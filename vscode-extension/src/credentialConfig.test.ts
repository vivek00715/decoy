import { SECRET_KEY_ANTHROPIC_API_KEY, SecretsHost } from "./apiKeyCommand";
import {
  buildProxyEnv,
  clearCredentials,
  ensureCredentials,
  GatewayCredentials,
  MODE_LABEL_DIRECT,
  MODE_LABEL_GATEWAY,
  runSetCredentials,
  SECRET_KEY_CREDENTIAL_MODE,
  SECRET_KEY_GATEWAY_AUTH_TOKEN,
  SECRET_KEY_GATEWAY_BASE_URL,
  SECRET_KEY_GATEWAY_SKIP_TLS_VERIFY,
} from "./credentialConfig";

/** In-memory fake, same role as apiKeyCommand.test.ts's makeFakeHost --
 * `store` doubles as the "secure storage" for persistence-across-restart
 * assertions (a fresh FakeSecretsHost pointed at the SAME store object
 * simulates "the IDE restarted, SecretStorage/PasswordSafe is still
 * there"). */
function makeFakeHost(options: {
  store?: Record<string, string>;
  choice?: string;
  gatewayToken?: string;
  gatewayUrl?: string;
  skipTlsVerify?: boolean;
  apiKey?: string;
} = {}): SecretsHost & { store: Record<string, string>; infoMessages: string[]; errorMessages: string[] } {
  const store = options.store ?? {};
  const infoMessages: string[] = [];
  const errorMessages: string[] = [];
  return {
    store,
    infoMessages,
    errorMessages,
    async getSecret(key) {
      return store[key];
    },
    async storeSecret(key, value) {
      store[key] = value;
    },
    async deleteSecret(key) {
      delete store[key];
    },
    async promptForApiKey() {
      return options.apiKey;
    },
    async promptForChoice() {
      return options.choice;
    },
    async promptForText(promptText) {
      if (promptText.includes("gateway URL") || promptText.includes("base URL")) {
        return options.gatewayUrl;
      }
      return options.gatewayToken;
    },
    async confirmDangerousToggle() {
      return options.skipTlsVerify ?? false;
    },
    showInfo(message) {
      infoMessages.push(message);
    },
    showError(message) {
      errorMessages.push(message);
    },
  };
}

describe("runSetCredentials", () => {
  it("cancel at the mode choice stores nothing", async () => {
    const host = makeFakeHost({ choice: undefined });
    const stored = await runSetCredentials(host);
    expect(stored).toBe(false);
    expect(Object.keys(host.store)).toHaveLength(0);
  });

  it("Direct mode stores the API key and mode flag", async () => {
    const host = makeFakeHost({ choice: MODE_LABEL_DIRECT, apiKey: "sk-ant-abc" });
    const stored = await runSetCredentials(host);
    expect(stored).toBe(true);
    expect(host.store[SECRET_KEY_ANTHROPIC_API_KEY]).toBe("sk-ant-abc");
    expect(host.store[SECRET_KEY_CREDENTIAL_MODE]).toBe("direct");
  });

  it("Org Gateway mode stores token, URL, mode flag, and TLS-skip=false by default", async () => {
    const host = makeFakeHost({
      choice: MODE_LABEL_GATEWAY,
      gatewayToken: "  gw-token-123  ",
      gatewayUrl: "https://api-eu1.aigateway.example.com",
    });
    const stored = await runSetCredentials(host);
    expect(stored).toBe(true);
    expect(host.store[SECRET_KEY_GATEWAY_AUTH_TOKEN]).toBe("gw-token-123");
    expect(host.store[SECRET_KEY_GATEWAY_BASE_URL]).toBe("https://api-eu1.aigateway.example.com");
    expect(host.store[SECRET_KEY_GATEWAY_SKIP_TLS_VERIFY]).toBe("false");
    expect(host.store[SECRET_KEY_CREDENTIAL_MODE]).toBe("gateway");
    expect(host.infoMessages[0]).not.toContain("gw-token-123");
  });

  it("Org Gateway mode with TLS-skip explicitly confirmed stores 'true' and warns in the confirmation", async () => {
    const host = makeFakeHost({
      choice: MODE_LABEL_GATEWAY,
      gatewayToken: "gw-token",
      gatewayUrl: "https://gw.example.com",
      skipTlsVerify: true,
    });
    await runSetCredentials(host);
    expect(host.store[SECRET_KEY_GATEWAY_SKIP_TLS_VERIFY]).toBe("true");
    expect(host.infoMessages[0]).toContain("DISABLED");
  });

  it("cancelling the token prompt in gateway mode stores nothing", async () => {
    const host = makeFakeHost({ choice: MODE_LABEL_GATEWAY, gatewayToken: undefined, gatewayUrl: "https://gw.example.com" });
    const stored = await runSetCredentials(host);
    expect(stored).toBe(false);
    expect(Object.keys(host.store)).toHaveLength(0);
  });

  it("cancelling the URL prompt in gateway mode stores nothing (not even the already-entered token)", async () => {
    const host = makeFakeHost({ choice: MODE_LABEL_GATEWAY, gatewayToken: "gw-token", gatewayUrl: undefined });
    const stored = await runSetCredentials(host);
    expect(stored).toBe(false);
    expect(Object.keys(host.store)).toHaveLength(0);
  });
});

describe("ensureCredentials", () => {
  it("resolves stored Direct-mode credentials without prompting", async () => {
    const host = makeFakeHost({
      store: { [SECRET_KEY_ANTHROPIC_API_KEY]: "sk-ant-existing", [SECRET_KEY_CREDENTIAL_MODE]: "direct" },
    });
    const creds = await ensureCredentials(host);
    expect(creds).toEqual({ mode: "direct", apiKey: "sk-ant-existing" });
  });

  it("resolves stored Org Gateway credentials without prompting", async () => {
    const host = makeFakeHost({
      store: {
        [SECRET_KEY_CREDENTIAL_MODE]: "gateway",
        [SECRET_KEY_GATEWAY_AUTH_TOKEN]: "gw-token",
        [SECRET_KEY_GATEWAY_BASE_URL]: "https://gw.example.com",
        [SECRET_KEY_GATEWAY_SKIP_TLS_VERIFY]: "true",
      },
    });
    const creds = await ensureCredentials(host);
    expect(creds).toEqual({ mode: "gateway", authToken: "gw-token", baseUrl: "https://gw.example.com", skipTlsVerify: true });
  });

  it("credential-mode choice persists across a simulated IDE restart (fresh host, same backing store)", async () => {
    const sharedStore: Record<string, string> = {};
    const firstSessionHost = makeFakeHost({
      store: sharedStore,
      choice: MODE_LABEL_GATEWAY,
      gatewayToken: "gw-token-persisted",
      gatewayUrl: "https://gw.example.com",
    });
    await runSetCredentials(firstSessionHost);

    // A brand-new host instance pointed at the same store simulates a
    // fresh extension activation after a restart -- no prompt functions
    // are wired to return anything, so if this had to prompt again the
    // test would fail (getSecret is the ONLY thing exercised).
    const secondSessionHost = makeFakeHost({ store: sharedStore });
    const creds = await ensureCredentials(secondSessionHost);
    expect(creds).toEqual({
      mode: "gateway",
      authToken: "gw-token-persisted",
      baseUrl: "https://gw.example.com",
      skipTlsVerify: false,
    });
  });

  it("falls through to re-prompting if mode says gateway but credentials are incomplete (never silently defaults to direct)", async () => {
    const host = makeFakeHost({
      store: { [SECRET_KEY_CREDENTIAL_MODE]: "gateway" }, // token/URL missing
      choice: MODE_LABEL_GATEWAY,
      gatewayToken: "recovered-token",
      gatewayUrl: "https://gw.example.com",
    });
    const creds = await ensureCredentials(host);
    expect(creds?.mode).toBe("gateway");
    expect((creds as GatewayCredentials).authToken).toBe("recovered-token");
  });

  it("returns undefined if the user cancels the prompt with nothing stored yet", async () => {
    const host = makeFakeHost({ choice: undefined });
    const creds = await ensureCredentials(host);
    expect(creds).toBeUndefined();
  });
});

describe("clearCredentials", () => {
  it("removes all credential keys for both modes", async () => {
    const host = makeFakeHost({
      store: {
        [SECRET_KEY_ANTHROPIC_API_KEY]: "sk-ant",
        [SECRET_KEY_GATEWAY_AUTH_TOKEN]: "gw-token",
        [SECRET_KEY_GATEWAY_BASE_URL]: "https://gw.example.com",
        [SECRET_KEY_GATEWAY_SKIP_TLS_VERIFY]: "true",
        [SECRET_KEY_CREDENTIAL_MODE]: "gateway",
      },
    });
    await clearCredentials(host);
    expect(Object.keys(host.store)).toHaveLength(0);
  });
});

describe("buildProxyEnv", () => {
  it("Direct mode: sets ANTHROPIC_API_KEY only, matching chat_proxy.py's ChatProxyConfig.from_env()", () => {
    const env = buildProxyEnv({ mode: "direct", apiKey: "sk-ant-abc" }, { PATH: "/usr/bin" } as NodeJS.ProcessEnv);
    expect(env.ANTHROPIC_API_KEY).toBe("sk-ant-abc");
    expect(env.ANTHROPIC_AUTH_TOKEN).toBeUndefined();
    expect(env.DECOY_ANTHROPIC_UPSTREAM_URL).toBeUndefined();
    expect(env.PATH).toBe("/usr/bin"); // base env preserved
  });

  it("Org Gateway mode: sets ANTHROPIC_AUTH_TOKEN, DECOY_ANTHROPIC_UPSTREAM_URL, and DECOY_UPSTREAM_SKIP_TLS_VERIFY -- the exact env vars chat_proxy.py's ChatProxyConfig.from_env() reads", () => {
    const creds: GatewayCredentials = {
      mode: "gateway",
      authToken: "gw-token-xyz",
      baseUrl: "https://api-eu1.aigateway.emirates.group",
      skipTlsVerify: true,
    };
    const env = buildProxyEnv(creds, {} as NodeJS.ProcessEnv);
    expect(env.ANTHROPIC_AUTH_TOKEN).toBe("gw-token-xyz");
    expect(env.DECOY_ANTHROPIC_UPSTREAM_URL).toBe("https://api-eu1.aigateway.emirates.group");
    expect(env.DECOY_UPSTREAM_SKIP_TLS_VERIFY).toBe("true");
    expect(env.ANTHROPIC_API_KEY).toBeUndefined();
  });

  it("Org Gateway mode with TLS-skip off sends the flag as the literal string 'false', not omitted", () => {
    const creds: GatewayCredentials = { mode: "gateway", authToken: "t", baseUrl: "https://gw.example.com", skipTlsVerify: false };
    const env = buildProxyEnv(creds, {} as NodeJS.ProcessEnv);
    expect(env.DECOY_UPSTREAM_SKIP_TLS_VERIFY).toBe("false");
  });
});

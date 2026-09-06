import { clearApiKey, ensureApiKey, runSetApiKey, SECRET_KEY_ANTHROPIC_API_KEY, SecretsHost } from "./apiKeyCommand";

function makeFakeHost(initial: Record<string, string> = {}, promptValue: string | undefined = undefined): SecretsHost & {
  store: Record<string, string>;
  infoMessages: string[];
  errorMessages: string[];
  promptCalls: string[];
} {
  const store: Record<string, string> = { ...initial };
  const infoMessages: string[] = [];
  const errorMessages: string[] = [];
  const promptCalls: string[] = [];
  return {
    store,
    infoMessages,
    errorMessages,
    promptCalls,
    async getSecret(key) {
      return store[key];
    },
    async storeSecret(key, value) {
      store[key] = value;
    },
    async deleteSecret(key) {
      delete store[key];
    },
    async promptForApiKey(promptText) {
      promptCalls.push(promptText);
      return promptValue;
    },
    showInfo(message) {
      infoMessages.push(message);
    },
    showError(message) {
      errorMessages.push(message);
    },
  };
}

describe("runSetApiKey", () => {
  it("stores a trimmed, non-empty key and confirms with a message never containing the key itself", async () => {
    const host = makeFakeHost({}, "  sk-ant-abc123  ");
    const stored = await runSetApiKey(host);
    expect(stored).toBe(true);
    expect(host.store[SECRET_KEY_ANTHROPIC_API_KEY]).toBe("sk-ant-abc123");
    expect(host.infoMessages).toHaveLength(1);
    expect(host.infoMessages[0]).not.toContain("sk-ant-abc123");
  });

  it("does nothing on cancel (undefined) and reports no key stored", async () => {
    const host = makeFakeHost({}, undefined);
    const stored = await runSetApiKey(host);
    expect(stored).toBe(false);
    expect(host.store[SECRET_KEY_ANTHROPIC_API_KEY]).toBeUndefined();
    expect(host.infoMessages).toHaveLength(0);
  });

  it("treats a blank/whitespace-only entry the same as cancel", async () => {
    const host = makeFakeHost({}, "   ");
    const stored = await runSetApiKey(host);
    expect(stored).toBe(false);
    expect(host.store[SECRET_KEY_ANTHROPIC_API_KEY]).toBeUndefined();
  });

  it("prompt text names the Pro-subscription-does-not-work distinction, not silently omitted", async () => {
    const host = makeFakeHost({}, "sk-ant-x");
    await runSetApiKey(host);
    expect(host.promptCalls[0]).toMatch(/Pro/);
    expect(host.promptCalls[0]).toMatch(/billed|billing/i);
  });
});

describe("clearApiKey", () => {
  it("removes the stored key", async () => {
    const host = makeFakeHost({ [SECRET_KEY_ANTHROPIC_API_KEY]: "sk-ant-existing" });
    await clearApiKey(host);
    expect(host.store[SECRET_KEY_ANTHROPIC_API_KEY]).toBeUndefined();
    expect(host.infoMessages).toHaveLength(1);
  });
});

describe("ensureApiKey", () => {
  it("returns the existing key without prompting when one is already stored", async () => {
    const host = makeFakeHost({ [SECRET_KEY_ANTHROPIC_API_KEY]: "sk-ant-existing" });
    const key = await ensureApiKey(host);
    expect(key).toBe("sk-ant-existing");
    expect(host.promptCalls).toHaveLength(0);
  });

  it("prompts and stores when no key exists yet, then returns it -- the 'Start Proxy just works' first-run path", async () => {
    const host = makeFakeHost({}, "sk-ant-first-time");
    const key = await ensureApiKey(host);
    expect(key).toBe("sk-ant-first-time");
    expect(host.promptCalls).toHaveLength(1);
    expect(host.store[SECRET_KEY_ANTHROPIC_API_KEY]).toBe("sk-ant-first-time");
  });

  it("returns undefined if the user cancels the first-run prompt, without storing anything", async () => {
    const host = makeFakeHost({}, undefined);
    const key = await ensureApiKey(host);
    expect(key).toBeUndefined();
    expect(host.store[SECRET_KEY_ANTHROPIC_API_KEY]).toBeUndefined();
  });
});

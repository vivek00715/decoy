import {
  buildClaudeSettingsUpdate,
  emptyClaudeSettings,
  parseClaudeSettings,
  removeClaudeSettingsEnvKey,
  serializeClaudeSettings,
} from "./claudeSettingsConfig";

describe("parseClaudeSettings", () => {
  it("returns empty settings for missing/blank content", () => {
    expect(parseClaudeSettings(undefined)).toEqual(emptyClaudeSettings());
    expect(parseClaudeSettings("")).toEqual(emptyClaudeSettings());
    expect(parseClaudeSettings("   ")).toEqual(emptyClaudeSettings());
  });

  it("throws on genuinely malformed JSON so the caller can warn instead of clobbering", () => {
    expect(() => parseClaudeSettings("{not json")).toThrow();
  });

  it("preserves unrelated top-level keys and existing env entries", () => {
    const parsed = parseClaudeSettings(
      JSON.stringify({ theme: "dark", enabledPlugins: { foo: true }, env: { EXISTING: "1" } }),
    );
    expect(parsed.theme).toBe("dark");
    expect(parsed.enabledPlugins).toEqual({ foo: true });
    expect(parsed.env).toEqual({ EXISTING: "1" });
  });
});

describe("buildClaudeSettingsUpdate", () => {
  it("adds ANTHROPIC_BASE_URL without touching unrelated env vars or top-level keys", () => {
    const existing = parseClaudeSettings(
      JSON.stringify({ theme: "dark", env: { SOME_OTHER_VAR: "keep-me" } }),
    );
    const { updated, overwritingExisting } = buildClaudeSettingsUpdate(
      existing,
      "ANTHROPIC_BASE_URL",
      "http://127.0.0.1:8787",
    );
    expect(updated.env).toEqual({ SOME_OTHER_VAR: "keep-me", ANTHROPIC_BASE_URL: "http://127.0.0.1:8787" });
    expect(updated.theme).toBe("dark");
    expect(overwritingExisting).toBe(false);
  });

  it("flags overwritingExisting only when the value actually changes", () => {
    const existing = parseClaudeSettings(JSON.stringify({ env: { ANTHROPIC_BASE_URL: "http://old:1" } }));
    const same = buildClaudeSettingsUpdate(existing, "ANTHROPIC_BASE_URL", "http://old:1");
    expect(same.overwritingExisting).toBe(false);

    const changed = buildClaudeSettingsUpdate(existing, "ANTHROPIC_BASE_URL", "http://new:2");
    expect(changed.overwritingExisting).toBe(true);
    expect(changed.updated.env).toEqual({ ANTHROPIC_BASE_URL: "http://new:2" });
  });

  it("works from a completely empty settings file", () => {
    const { updated } = buildClaudeSettingsUpdate(emptyClaudeSettings(), "ANTHROPIC_BASE_URL", "http://127.0.0.1:8787");
    expect(updated).toEqual({ env: { ANTHROPIC_BASE_URL: "http://127.0.0.1:8787" } });
  });
});

describe("removeClaudeSettingsEnvKey", () => {
  it("removes only the named key, leaving other env vars and top-level keys intact", () => {
    const existing = parseClaudeSettings(
      JSON.stringify({ theme: "dark", env: { ANTHROPIC_BASE_URL: "http://x", KEEP: "1" } }),
    );
    const result = removeClaudeSettingsEnvKey(existing, "ANTHROPIC_BASE_URL");
    expect(result.env).toEqual({ KEEP: "1" });
    expect(result.theme).toBe("dark");
  });

  it("is a no-op when the key isn't present", () => {
    const existing = parseClaudeSettings(JSON.stringify({ env: { KEEP: "1" } }));
    const result = removeClaudeSettingsEnvKey(existing, "ANTHROPIC_BASE_URL");
    expect(result).toEqual(existing);
  });
});

describe("serializeClaudeSettings", () => {
  it("round-trips through parse", () => {
    const settings = { theme: "dark", env: { ANTHROPIC_BASE_URL: "http://127.0.0.1:8787" } };
    const text = serializeClaudeSettings(settings);
    expect(parseClaudeSettings(text)).toEqual(settings);
  });
});

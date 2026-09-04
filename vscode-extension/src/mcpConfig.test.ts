import {
  buildDecoyProxyArgs,
  buildMcpConfigUpdate,
  emptyMcpConfig,
  parseMcpConfig,
  serializeMcpConfig,
} from "./mcpConfig";

describe("parseMcpConfig", () => {
  it("returns an empty config for undefined/empty text", () => {
    expect(parseMcpConfig(undefined)).toEqual(emptyMcpConfig());
    expect(parseMcpConfig("")).toEqual(emptyMcpConfig());
    expect(parseMcpConfig("   ")).toEqual(emptyMcpConfig());
  });

  it("parses a well-formed existing .mcp.json", () => {
    const text = JSON.stringify({
      mcpServers: { other: { command: "npx", args: ["-y", "some-server"] } },
    });
    const result = parseMcpConfig(text);
    expect(result.mcpServers.other).toEqual({ command: "npx", args: ["-y", "some-server"] });
  });

  it("defaults mcpServers to {} if missing from an otherwise valid file", () => {
    const result = parseMcpConfig(JSON.stringify({ someOtherKey: true }));
    expect(result.mcpServers).toEqual({});
    expect(result.someOtherKey).toBe(true);
  });

  it("throws on malformed JSON so the caller can warn instead of clobbering", () => {
    expect(() => parseMcpConfig("{ not valid json")).toThrow();
  });

  it("throws when the top-level value isn't an object", () => {
    expect(() => parseMcpConfig("[1,2,3]")).toThrow();
  });
});

describe("buildDecoyProxyArgs", () => {
  it("matches decoy proxy's real CLI shape: proxy --session SESSION target [args...]", () => {
    expect(buildDecoyProxyArgs("my-session", "npx", ["-y", "@some/mcp-server"])).toEqual([
      "proxy",
      "--session",
      "my-session",
      "npx",
      "-y",
      "@some/mcp-server",
    ]);
  });

  it("handles a target with no extra args", () => {
    expect(buildDecoyProxyArgs("s1", "/usr/local/bin/some-server", [])).toEqual([
      "proxy",
      "--session",
      "s1",
      "/usr/local/bin/some-server",
    ]);
  });
});

describe("buildMcpConfigUpdate", () => {
  it("adds a new entry to an empty config", () => {
    const result = buildMcpConfigUpdate({
      existing: emptyMcpConfig(),
      serverName: "decoy",
      binaryPath: "/ext/bin/decoy-proxy",
      sessionId: "vscode-session",
      targetCommand: "npx",
      targetArgs: ["-y", "@acme/server"],
    });
    expect(result.overwritingExisting).toBe(false);
    expect(result.updated.mcpServers.decoy).toEqual({
      command: "/ext/bin/decoy-proxy",
      args: ["proxy", "--session", "vscode-session", "npx", "-y", "@acme/server"],
    });
  });

  it("preserves unrelated existing mcpServers entries", () => {
    const existing = parseMcpConfig(
      JSON.stringify({ mcpServers: { unrelated: { command: "foo", args: [] } } }),
    );
    const result = buildMcpConfigUpdate({
      existing,
      serverName: "decoy",
      binaryPath: "/bin/decoy-proxy",
      sessionId: "s",
      targetCommand: "t",
      targetArgs: [],
    });
    expect(result.updated.mcpServers.unrelated).toEqual({ command: "foo", args: [] });
    expect(result.updated.mcpServers.decoy).toBeDefined();
  });

  it("preserves unrelated top-level keys in the file", () => {
    const existing = parseMcpConfig(JSON.stringify({ mcpServers: {}, someFutureKey: 42 }));
    const result = buildMcpConfigUpdate({
      existing,
      serverName: "decoy",
      binaryPath: "/bin/decoy-proxy",
      sessionId: "s",
      targetCommand: "t",
      targetArgs: [],
    });
    expect(result.updated.someFutureKey).toBe(42);
  });

  it("flags overwritingExisting and replaces the entry when decoy already has one", () => {
    const existing = parseMcpConfig(
      JSON.stringify({
        mcpServers: { decoy: { command: "/old/path/decoy-proxy", args: ["proxy", "--session", "old"] } },
      }),
    );
    const result = buildMcpConfigUpdate({
      existing,
      serverName: "decoy",
      binaryPath: "/new/path/decoy-proxy",
      sessionId: "new-session",
      targetCommand: "npx",
      targetArgs: [],
    });
    expect(result.overwritingExisting).toBe(true);
    expect(result.updated.mcpServers.decoy.command).toBe("/new/path/decoy-proxy");
  });

  it("never mutates the input existing config object", () => {
    const existing = parseMcpConfig(JSON.stringify({ mcpServers: { a: { command: "x", args: [] } } }));
    const snapshot = JSON.parse(JSON.stringify(existing));
    buildMcpConfigUpdate({
      existing,
      serverName: "decoy",
      binaryPath: "/bin/decoy-proxy",
      sessionId: "s",
      targetCommand: "t",
      targetArgs: [],
    });
    expect(existing).toEqual(snapshot);
  });
});

describe("serializeMcpConfig", () => {
  it("produces pretty-printed JSON ending in a trailing newline", () => {
    const text = serializeMcpConfig({ mcpServers: { decoy: { command: "c", args: [] } } });
    expect(text.endsWith("\n")).toBe(true);
    expect(JSON.parse(text)).toEqual({ mcpServers: { decoy: { command: "c", args: [] } } });
  });

  it("round-trips through parseMcpConfig", () => {
    const original = emptyMcpConfig();
    const result = buildMcpConfigUpdate({
      existing: original,
      serverName: "decoy",
      binaryPath: "/bin/decoy-proxy",
      sessionId: "s1",
      targetCommand: "npx",
      targetArgs: ["-y", "srv"],
    });
    const text = serializeMcpConfig(result.updated);
    expect(parseMcpConfig(text)).toEqual(result.updated);
  });
});

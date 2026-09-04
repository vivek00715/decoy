/**
 * Pure logic for writing/merging Decoy's `decoy-proxy` binary into a
 * workspace's `.mcp.json` (Claude Code's project-scoped MCP server config
 * file, read from the workspace root -- see README.md's "Installing the
 * MCP proxy: standalone binary" section for the general shape; the exact
 * `.mcp.json` filename/location convention itself is Claude Code's own,
 * documented at https://docs.claude.com/en/docs/claude-code/mcp, not
 * something this repo's own docs pin down further -- flagged as an
 * assumption rather than guessed silently).
 *
 * Kept host-free (no `vscode` import) so the merge logic -- the riskiest
 * part of this feature, since it writes to a file that may already have
 * unrelated hand-edited entries in it -- is unit-testable the same way
 * panelController.ts's message handling is: real logic in a plain
 * function, real vscode.* calls only in extension.ts.
 */

/** Shape of a single MCP server entry in `.mcp.json`. Only the fields
 * Decoy itself ever writes are typed strictly; `[key: string]: unknown`
 * preserves any other fields (e.g. `env`) a user or another tool already
 * set on an entry we're merging into, rather than silently dropping them. */
export interface McpServerEntry {
  command: string;
  args: string[];
  [key: string]: unknown;
}

export interface McpConfigFile {
  mcpServers: Record<string, McpServerEntry>;
  [key: string]: unknown;
}

export function emptyMcpConfig(): McpConfigFile {
  return { mcpServers: {} };
}

/**
 * Parse an existing `.mcp.json`'s text. Missing/malformed content fails
 * safe to an empty config (matching this codebase's readOverrides/
 * readAuditLog convention elsewhere) -- callers decide whether "malformed"
 * should block the write instead (see buildMcpConfigUpdate below, which
 * treats a parse failure as a reason to warn rather than clobber).
 */
export function parseMcpConfig(text: string | undefined): McpConfigFile {
  if (!text || text.trim().length === 0) {
    return emptyMcpConfig();
  }
  const parsed: unknown = JSON.parse(text);
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    throw new Error(".mcp.json does not contain a JSON object");
  }
  const obj = parsed as Record<string, unknown>;
  const mcpServers =
    typeof obj.mcpServers === "object" && obj.mcpServers !== null
      ? (obj.mcpServers as Record<string, McpServerEntry>)
      : {};
  return { ...obj, mcpServers };
}

/** decoy proxy's real CLI shape (src/decoy/cli.py's `proxy` subparser,
 * confirmed against the packaged binary's entry point `decoy.cli:main` in
 * scripts/build_binary.py): `decoy-proxy proxy --session SESSION
 * target_command [target_args...]`. No `--` separator before
 * target_command -- it's a plain positional, and target_args is
 * argparse.REMAINDER. */
export function buildDecoyProxyArgs(
  sessionId: string,
  targetCommand: string,
  targetArgs: string[],
): string[] {
  return ["proxy", "--session", sessionId, targetCommand, ...targetArgs];
}

export interface McpConfigUpdateInput {
  /** Parsed existing config (or emptyMcpConfig() if none existed yet). */
  existing: McpConfigFile;
  /** Name to register the server entry under, e.g. "decoy". */
  serverName: string;
  /** Absolute path to the bundled decoy-proxy binary. */
  binaryPath: string;
  sessionId: string;
  targetCommand: string;
  targetArgs: string[];
}

export interface McpConfigUpdateResult {
  /** The full config object to serialize and write. */
  updated: McpConfigFile;
  /** True if an existing entry under `serverName` is being overwritten
   * (the caller should mention this explicitly in its confirmation UI). */
  overwritingExisting: boolean;
  /** The entry that was written, for display in a confirmation message. */
  entry: McpServerEntry;
}

/**
 * Merge a Decoy server entry into an existing config WITHOUT touching any
 * other key in `mcpServers` (or any other top-level key in the file) --
 * the whole point of merging rather than overwriting `.mcp.json` wholesale.
 */
export function buildMcpConfigUpdate(input: McpConfigUpdateInput): McpConfigUpdateResult {
  const entry: McpServerEntry = {
    command: input.binaryPath,
    args: buildDecoyProxyArgs(input.sessionId, input.targetCommand, input.targetArgs),
  };
  const overwritingExisting = Object.prototype.hasOwnProperty.call(
    input.existing.mcpServers,
    input.serverName,
  );
  const updated: McpConfigFile = {
    ...input.existing,
    mcpServers: {
      ...input.existing.mcpServers,
      [input.serverName]: entry,
    },
  };
  return { updated, overwritingExisting, entry };
}

export function serializeMcpConfig(config: McpConfigFile): string {
  return JSON.stringify(config, null, 2) + "\n";
}

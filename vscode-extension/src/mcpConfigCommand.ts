import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";

import { buildMcpConfigUpdate, parseMcpConfig, serializeMcpConfig } from "./mcpConfig";

/** Claude Code's project-scoped MCP server config file: `.mcp.json` at
 * the workspace root. See README.md's "Installing the MCP proxy" section
 * for the general `command`-points-at-the-binary shape this mirrors; the
 * `.mcp.json` filename/location itself is Claude Code's own documented
 * convention (docs.claude.com/en/docs/claude-code/mcp), not something
 * this repo's docs pin down beyond "your MCP client's config" -- if
 * Claude Code changes that convention this constant is the one place to
 * update. */
export const MCP_CONFIG_FILENAME = ".mcp.json";

const DEFAULT_SERVER_NAME = "decoy";

/** Locate this extension's bundled binary for the current machine,
 * following the bin/<platform>-<arch>/decoy-proxy(.exe) convention
 * scripts/bundle-binaries.js writes to. Returns undefined if nothing was
 * bundled for this platform (e.g. this dev environment only built macOS). */
export function findBundledBinary(extensionPath: string): string | undefined {
  const binaryName = process.platform === "win32" ? "decoy-proxy.exe" : "decoy-proxy";
  const candidate = path.join(extensionPath, "bin", `${process.platform}-${process.arch}`, binaryName);
  return fs.existsSync(candidate) ? candidate : undefined;
}

function getWorkspaceRoot(): string | undefined {
  const folders = vscode.workspace.workspaceFolders;
  if (!folders || folders.length === 0) {
    return undefined;
  }
  return folders[0].uri.fsPath;
}

/**
 * The full interactive flow for `decoy.configureMcpProxy`: confirm with
 * the user before writing (this WRITES a config file, so it gets the
 * same explicit-confirmation bar as panelController.ts's destructive
 * actions), prompt for the real target MCP server this proxy should
 * wrap (decoy-proxy cannot infer this -- see mcp_proxy.py), merge into
 * `.mcp.json`, and show exactly what changed.
 */
export async function runConfigureMcpProxy(context: vscode.ExtensionContext): Promise<void> {
  const rootDir = getWorkspaceRoot();
  if (!rootDir) {
    void vscode.window.showErrorMessage(
      "Decoy: open a workspace folder first -- .mcp.json is written relative to the workspace root.",
    );
    return;
  }

  const binaryPath = findBundledBinary(context.extensionPath);
  if (!binaryPath) {
    void vscode.window.showErrorMessage(
      `Decoy: no bundled decoy-proxy binary found for this platform (${process.platform}-${process.arch}). ` +
        "This build of the extension may not include a binary for your OS/architecture.",
    );
    return;
  }

  const targetCommand = await vscode.window.showInputBox({
    title: "Decoy: real target MCP server command",
    prompt:
      "decoy-proxy sits in front of a REAL target MCP server and masks its results -- " +
      "enter the command that launches that real server (e.g. `npx`).",
    placeHolder: "npx",
    ignoreFocusOut: true,
  });
  if (!targetCommand || targetCommand.trim().length === 0) {
    return;
  }

  const targetArgsRaw = await vscode.window.showInputBox({
    title: "Decoy: target MCP server arguments (optional)",
    prompt: "Space-separated arguments passed through to the target command above.",
    placeHolder: "-y @acme/some-mcp-server",
    ignoreFocusOut: true,
  });
  const targetArgs = (targetArgsRaw ?? "").trim().length > 0 ? targetArgsRaw!.trim().split(/\s+/) : [];

  const sessionId = `vscode-${path.basename(rootDir)}`;

  const configPath = path.join(rootDir, MCP_CONFIG_FILENAME);
  let existingText: string | undefined;
  try {
    existingText = fs.readFileSync(configPath, "utf8");
  } catch {
    existingText = undefined;
  }

  let existing;
  try {
    existing = parseMcpConfig(existingText);
  } catch (err) {
    void vscode.window.showErrorMessage(
      `Decoy: ${MCP_CONFIG_FILENAME} exists but isn't valid JSON (${
        err instanceof Error ? err.message : String(err)
      }). Fix or remove it before configuring the MCP proxy.`,
    );
    return;
  }

  const { updated, overwritingExisting, entry } = buildMcpConfigUpdate({
    existing,
    serverName: DEFAULT_SERVER_NAME,
    binaryPath,
    sessionId,
    targetCommand: targetCommand.trim(),
    targetArgs,
  });

  const overwriteNote = overwritingExisting
    ? `\n\nThis REPLACES the existing "${DEFAULT_SERVER_NAME}" entry already in ${MCP_CONFIG_FILENAME}.`
    : "";
  const confirmMessage =
    `Decoy will write the following MCP server entry to ${MCP_CONFIG_FILENAME} at the workspace root:` +
    `\n\n"${DEFAULT_SERVER_NAME}": ${JSON.stringify(entry, null, 2)}${overwriteNote}` +
    `\n\nNote: this does not connect the server automatically. After writing, run "claude" ` +
    `interactively in this directory and approve the "${DEFAULT_SERVER_NAME}" server when prompted.`;

  const choice = await vscode.window.showWarningMessage(confirmMessage, { modal: true }, "Write Config");
  if (choice !== "Write Config") {
    return;
  }

  fs.writeFileSync(configPath, serializeMcpConfig(updated), "utf8");

  // Writing .mcp.json does NOT auto-connect the server -- confirmed
  // directly against a real `claude` CLI: an entry added this way shows
  // as "Pending approval" until a human approves it interactively.
  // Naming the exact fix ("run `claude` ... and approve") here, not just
  // that something is needed, since a user reading only "restart Claude
  // Code" would reasonably expect that alone to be enough -- it isn't.
  void vscode.window.showInformationMessage(
    `Decoy: wrote "${DEFAULT_SERVER_NAME}" MCP server entry to ${configPath}. ` +
      `This does NOT connect it automatically -- .mcp.json entries require one-time approval. ` +
      `Run "claude" interactively in this directory and approve the "${DEFAULT_SERVER_NAME}" server when prompted.`,
  );
}

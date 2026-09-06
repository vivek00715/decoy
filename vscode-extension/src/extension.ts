import * as crypto from "crypto";
import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";

import { ensureApiKey, runSetApiKey, SecretsHost } from "./apiKeyCommand";
import { groupByRequest, readAuditLog } from "./auditLog";
import { ChatProxyManager, ProxyState } from "./chatProxyManager";
import {
  buildClaudeSettingsUpdate,
  parseClaudeSettings,
  removeClaudeSettingsEnvKey,
  serializeClaudeSettings,
} from "./claudeSettingsConfig";
import { findBundledBinary, runConfigureMcpProxy } from "./mcpConfigCommand";
import { approvalActionMessage, checkMcpApproval, McpApprovalResult } from "./mcpApproval";
import { PanelController, PanelHost, WebviewMessage } from "./panelController";

const DEFAULT_CHAT_PROXY_PORT = 8787;
const CLAUDE_SETTINGS_RELATIVE_PATH = path.join(".claude", "settings.json");

const FIRST_RUN_NOTICE_KEY = "decoy.firstRunNoticeShown";

// Deliberately no absolute-privacy claims -- see the core design
// principles in the project spec and Phase 11's forthcoming "what this
// protects against, and what it doesn't" doc.
const FIRST_RUN_NOTICE_TEXT =
  "Decoy masks PII it detects (via regex/NER, manual overrides, and shape-based rules) before " +
  "it reaches an LLM, and unmasks the response. It does not guarantee protection against " +
  "re-identification from surrounding context, and automated relevance classification is " +
  "imperfect -- that's why manual overrides exist. All audit/session data is stored locally " +
  "only, and you can clear it any time from the Decoy panel.";

function getWorkspaceRoot(): string | undefined {
  const folders = vscode.workspace.workspaceFolders;
  if (!folders || folders.length === 0) {
    return undefined;
  }
  return folders[0].uri.fsPath;
}

/**
 * Owns the chat proxy's ENTIRE lifecycle plus the two pieces of manual
 * setup that used to surround it -- see chatProxyManager.ts/
 * apiKeyCommand.ts/claudeSettingsConfig.ts/mcpApproval.ts for the
 * host-free, independently-tested logic this class wires up to real
 * vscode.* APIs (SecretStorage, showInputBox, the filesystem). This
 * class itself is the "glue" layer that cannot be exercised without a
 * real VS Code host -- its correctness rests on those four modules'
 * real tests plus manual confirmation (see MANUAL_TEST.md).
 */
class ChatProxyController {
  private manager: ChatProxyManager | undefined;
  private lastApproval: McpApprovalResult | undefined;
  private readonly port = DEFAULT_CHAT_PROXY_PORT;

  constructor(
    private readonly context: vscode.ExtensionContext,
    private readonly onStateChange: () => void,
  ) {}

  private secretsHost(): SecretsHost {
    return {
      getSecret: (key) => Promise.resolve(this.context.secrets.get(key)),
      storeSecret: (key, value) => Promise.resolve(this.context.secrets.store(key, value)),
      deleteSecret: (key) => Promise.resolve(this.context.secrets.delete(key)),
      promptForApiKey: async (promptText) =>
        vscode.window.showInputBox({ prompt: promptText, password: true, ignoreFocusOut: true }),
      showInfo: (message) => void vscode.window.showInformationMessage(message),
      showError: (message) => void vscode.window.showErrorMessage(message),
    };
  }

  getProxyStatus(): { state: ProxyState; detail?: string; port: number } {
    return {
      state: this.manager?.getState() ?? "stopped",
      detail: this.manager?.getLastError(),
      port: this.port,
    };
  }

  getApprovalStatus(): { status: string; message?: string } | undefined {
    if (!this.lastApproval) {
      return undefined;
    }
    if (this.lastApproval.status === "pending") {
      return { status: "pending", message: approvalActionMessage("decoy") };
    }
    return { status: this.lastApproval.status };
  }

  /** Shells out to `claude mcp get decoy` -- not free, so callers trigger
   * this explicitly (panel open, manual refresh) rather than on every
   * file-watcher tick. Fire-and-forget: calls onStateChange() when the
   * result lands so the panel re-renders with the answer. */
  checkApproval(): void {
    const rootDir = getWorkspaceRoot();
    if (!rootDir) {
      return;
    }
    void checkMcpApproval("decoy", rootDir).then((result) => {
      this.lastApproval = result;
      this.onStateChange();
    });
  }

  private ensureManager(): ChatProxyManager {
    if (!this.manager) {
      const binaryPath = findBundledBinary(this.context.extensionPath);
      // Falls back to a bare "decoy" command (relies on PATH) only if no
      // bundled binary matches this platform/arch -- findBundledBinary
      // already covers the normal case via the Phase 12 bundled binary,
      // never assuming a Python/CLI install on PATH as the primary path.
      const command = binaryPath ?? "decoy";
      const args = binaryPath ? ["chat-proxy", "--port", String(this.port)] : ["chat-proxy", "--port", String(this.port)];
      this.manager = new ChatProxyManager(command, args, { onStateChange: () => this.onStateChange() });
    }
    return this.manager;
  }

  async start(): Promise<void> {
    const key = await ensureApiKey(this.secretsHost());
    if (!key) {
      return; // user cancelled the first-run key prompt
    }
    this.ensureManager().start({ ...process.env, ANTHROPIC_API_KEY: key } as NodeJS.ProcessEnv);
    this.writeBaseUrlIntoClaudeSettings();
  }

  async stop(): Promise<void> {
    await this.manager?.stopAndWait();
    this.removeBaseUrlFromClaudeSettings();
  }

  async restart(): Promise<void> {
    const key = await ensureApiKey(this.secretsHost());
    if (!key) {
      return;
    }
    await this.ensureManager().restart({ ...process.env, ANTHROPIC_API_KEY: key } as NodeJS.ProcessEnv);
    this.writeBaseUrlIntoClaudeSettings();
  }

  async setApiKey(): Promise<void> {
    await runSetApiKey(this.secretsHost());
  }

  /**
   * Writes ANTHROPIC_BASE_URL into .claude/settings.json's `env` block --
   * confirmed directly against a real `claude` CLI (see
   * claudeSettingsConfig.ts's module docstring) that this is read and
   * applied with no shell export required. No separate confirmation
   * dialog for this specific write (unlike .mcp.json's): it's a same-
   * click side effect of the "Start" action the user already explicitly
   * triggered, not a standalone silent write -- the button's own hint
   * text in the panel says this is what happens.
   */
  private writeBaseUrlIntoClaudeSettings(): void {
    const rootDir = getWorkspaceRoot();
    if (!rootDir) {
      return;
    }
    const settingsPath = path.join(rootDir, CLAUDE_SETTINGS_RELATIVE_PATH);
    let existingText: string | undefined;
    try {
      existingText = fs.readFileSync(settingsPath, "utf8");
    } catch {
      existingText = undefined;
    }
    let existing;
    try {
      existing = parseClaudeSettings(existingText);
    } catch {
      void vscode.window.showWarningMessage(
        `Decoy: ${CLAUDE_SETTINGS_RELATIVE_PATH} exists but isn't valid JSON -- not touching it. ` +
          `Set ANTHROPIC_BASE_URL=http://127.0.0.1:${this.port} there yourself.`,
      );
      return;
    }
    const { updated } = buildClaudeSettingsUpdate(existing, "ANTHROPIC_BASE_URL", `http://127.0.0.1:${this.port}`);
    fs.mkdirSync(path.dirname(settingsPath), { recursive: true });
    fs.writeFileSync(settingsPath, serializeClaudeSettings(updated), "utf8");
  }

  private removeBaseUrlFromClaudeSettings(): void {
    const rootDir = getWorkspaceRoot();
    if (!rootDir) {
      return;
    }
    const settingsPath = path.join(rootDir, CLAUDE_SETTINGS_RELATIVE_PATH);
    let existingText: string | undefined;
    try {
      existingText = fs.readFileSync(settingsPath, "utf8");
    } catch {
      return;
    }
    let existing;
    try {
      existing = parseClaudeSettings(existingText);
    } catch {
      return;
    }
    const updated = removeClaudeSettingsEnvKey(existing, "ANTHROPIC_BASE_URL");
    fs.writeFileSync(settingsPath, serializeClaudeSettings(updated), "utf8");
  }

  /** Awaited from deactivate() -- see that function's own comment for
   * why fire-and-forget here would risk an orphaned process. */
  async dispose(): Promise<void> {
    await this.manager?.dispose();
  }
}

class DecoyViewProvider implements vscode.WebviewViewProvider {
  private controller: PanelController | undefined;

  constructor(
    private readonly statusBarItem: vscode.StatusBarItem,
    private readonly extensionUri: vscode.Uri,
    private readonly proxyController: ChatProxyController,
  ) {}

  resolveWebviewView(webviewView: vscode.WebviewView): void {
    const codiconsRoot = vscode.Uri.joinPath(this.extensionUri, "media", "codicons");
    webviewView.webview.options = {
      enableScripts: true,
      localResourceRoots: [codiconsRoot],
    };
    const codiconsUri = webviewView.webview
      .asWebviewUri(vscode.Uri.joinPath(codiconsRoot, "codicon.css"))
      .toString();

    const host: PanelHost = {
      get rootDir() {
        return getWorkspaceRoot() ?? "";
      },
      cspSource: webviewView.webview.cspSource,
      codiconsUri,
      generateNonce: () => crypto.randomBytes(16).toString("hex"),
      postHtml: (html: string) => {
        webviewView.webview.html = html;
        this.updateStatusBar();
      },
      confirmDestructive: async (message: string) => {
        const choice = await vscode.window.showWarningMessage(message, { modal: true }, "Confirm");
        return choice === "Confirm";
      },
      getProxyStatus: () => this.proxyController.getProxyStatus(),
      getApprovalStatus: () => this.proxyController.getApprovalStatus(),
    };

    this.controller = new PanelController(host);

    webviewView.webview.onDidReceiveMessage((message: WebviewMessage | { command: string }) => {
      switch (message.command) {
        case "startChatProxy":
          void this.proxyController.start().then(() => this.refresh());
          return;
        case "stopChatProxy":
          void this.proxyController.stop().then(() => this.refresh());
          return;
        case "restartChatProxy":
          void this.proxyController.restart().then(() => this.refresh());
          return;
        case "setApiKey":
          void this.proxyController.setApiKey();
          return;
        default:
          void this.controller?.handleMessage(message as WebviewMessage);
      }
    });

    this.controller.refresh();
    // Checking approval shells out to the real `claude` CLI (not free) --
    // done once when the panel actually opens, not on every file-watcher
    // tick; refresh() re-renders again once the async result lands.
    this.proxyController.checkApproval();
  }

  refresh(): void {
    this.controller?.refresh();
  }

  async clearAll(): Promise<void> {
    await this.controller?.handleMessage({ command: "clearAll" });
  }

  async clearSessionDataOnly(): Promise<void> {
    await this.controller?.handleMessage({ command: "clearSessionDataOnly" });
  }

  async clearSession(sessionId: string): Promise<void> {
    await this.controller?.handleMessage({ command: "clearSession", sessionId });
  }

  private updateStatusBar(): void {
    const rootDir = getWorkspaceRoot();
    if (!rootDir) {
      this.statusBarItem.text = "$(shield) Decoy";
      return;
    }
    const log = readAuditLog(rootDir);
    const groups = groupByRequest(log.entries);
    this.statusBarItem.text = `$(shield) Decoy: ${groups.length} request(s)`;
  }
}

let activeChatProxyController: ChatProxyController | undefined;

export function activate(context: vscode.ExtensionContext): void {
  const statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  statusBarItem.text = "$(shield) Decoy";
  statusBarItem.tooltip = "Decoy: click to open the masking trace panel";
  statusBarItem.command = "workbench.view.extension.decoy";
  statusBarItem.show();
  context.subscriptions.push(statusBarItem);

  const proxyController = new ChatProxyController(context, () => provider.refresh());
  activeChatProxyController = proxyController;

  const provider = new DecoyViewProvider(statusBarItem, context.extensionUri, proxyController);
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider("decoy.tracePanel", provider),
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("decoy.setApiKey", async () => {
      await proxyController.setApiKey();
    }),
  );
  context.subscriptions.push(
    vscode.commands.registerCommand("decoy.startChatProxy", async () => {
      await proxyController.start();
      provider.refresh();
    }),
  );
  context.subscriptions.push(
    vscode.commands.registerCommand("decoy.stopChatProxy", async () => {
      await proxyController.stop();
      provider.refresh();
    }),
  );
  context.subscriptions.push(
    vscode.commands.registerCommand("decoy.restartChatProxy", async () => {
      await proxyController.restart();
      provider.refresh();
    }),
  );

  // Live auto-refresh: watches .decoy/audit.enc and .decoy/overrides.json
  // so the panel picks up a new request or override change as soon as it
  // lands on disk, without the user needing to click Refresh -- this is
  // what makes the panel's "live" status dot true rather than decorative.
  const rootDir = getWorkspaceRoot();
  if (rootDir) {
    const watcher = vscode.workspace.createFileSystemWatcher(
      new vscode.RelativePattern(rootDir, ".decoy/{audit.enc,overrides.json}"),
    );
    watcher.onDidChange(() => provider.refresh());
    watcher.onDidCreate(() => provider.refresh());
    watcher.onDidDelete(() => provider.refresh());
    context.subscriptions.push(watcher);
  }

  context.subscriptions.push(
    vscode.commands.registerCommand("decoy.refresh", () => provider.refresh()),
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("decoy.clearAll", async () => {
      await provider.clearAll();
    }),
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("decoy.clearSessionDataOnly", async () => {
      await provider.clearSessionDataOnly();
    }),
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("decoy.clearSession", async () => {
      const rootDir = getWorkspaceRoot();
      if (!rootDir) {
        return;
      }
      const log = readAuditLog(rootDir);
      const groups = groupByRequest(log.entries);
      const sessionIds = Array.from(new Set(groups.map((g) => g.session_id)));
      if (sessionIds.length === 0) {
        void vscode.window.showInformationMessage("Decoy: no sessions recorded yet.");
        return;
      }
      const picked = await vscode.window.showQuickPick(sessionIds, {
        placeHolder: "Select a session to clear",
      });
      if (!picked) {
        return;
      }
      await provider.clearSession(picked);
    }),
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("decoy.showFirstRunNotice", () => {
      void vscode.window.showInformationMessage(FIRST_RUN_NOTICE_TEXT, "Got it");
    }),
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("decoy.configureMcpProxy", async () => {
      await runConfigureMcpProxy(context);
    }),
  );

  if (!context.globalState.get<boolean>(FIRST_RUN_NOTICE_KEY, false)) {
    void vscode.window.showInformationMessage(FIRST_RUN_NOTICE_TEXT, "Got it");
    void context.globalState.update(FIRST_RUN_NOTICE_KEY, true);
  }
}

export async function deactivate(): Promise<void> {
  // MUST be awaited, not fire-and-forget: VS Code can tear down the
  // extension host process shortly after deactivate() returns, and a
  // chat proxy child process that was only just sent SIGTERM (not yet
  // reaped) would then survive the editor closing -- exactly the
  // orphaned-background-process failure mode this phase exists to
  // prevent. See ChatProxyManager.dispose()/stopAndWait() for the
  // SIGTERM-then-SIGKILL-on-timeout escalation this awaits through.
  await activeChatProxyController?.dispose();
  activeChatProxyController = undefined;
}

import * as crypto from "crypto";
import * as vscode from "vscode";

import { groupByRequest, readAuditLog } from "./auditLog";
import { PanelController, PanelHost, WebviewMessage } from "./panelController";

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

class DecoyViewProvider implements vscode.WebviewViewProvider {
  private controller: PanelController | undefined;

  constructor(private readonly statusBarItem: vscode.StatusBarItem) {}

  resolveWebviewView(webviewView: vscode.WebviewView): void {
    webviewView.webview.options = { enableScripts: true };

    const host: PanelHost = {
      get rootDir() {
        return getWorkspaceRoot() ?? "";
      },
      cspSource: webviewView.webview.cspSource,
      generateNonce: () => crypto.randomBytes(16).toString("hex"),
      postHtml: (html: string) => {
        webviewView.webview.html = html;
        this.updateStatusBar();
      },
      confirmDestructive: async (message: string) => {
        const choice = await vscode.window.showWarningMessage(message, { modal: true }, "Confirm");
        return choice === "Confirm";
      },
    };

    this.controller = new PanelController(host);

    webviewView.webview.onDidReceiveMessage((message: WebviewMessage) => {
      void this.controller?.handleMessage(message);
    });

    this.controller.refresh();
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

export function activate(context: vscode.ExtensionContext): void {
  const statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  statusBarItem.text = "$(shield) Decoy";
  statusBarItem.tooltip = "Decoy: click to open the masking trace panel";
  statusBarItem.command = "workbench.view.extension.decoy";
  statusBarItem.show();
  context.subscriptions.push(statusBarItem);

  const provider = new DecoyViewProvider(statusBarItem);
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider("decoy.tracePanel", provider),
  );

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

  if (!context.globalState.get<boolean>(FIRST_RUN_NOTICE_KEY, false)) {
    void vscode.window.showInformationMessage(FIRST_RUN_NOTICE_TEXT, "Got it");
    void context.globalState.update(FIRST_RUN_NOTICE_KEY, true);
  }
}

export function deactivate(): void {
  // No teardown needed: all state lives in local files the Python service
  // owns; this extension only reads/writes them, it doesn't hold a
  // long-lived connection to anything.
}

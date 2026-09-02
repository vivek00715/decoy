import { clearAuditAll, clearAuditSession, groupByRequest, readAuditLog } from "./auditLog";
import { clearOverrides, readOverrides, writeOverrides } from "./overrides";
import { clearVaultAll, clearVaultSession } from "./vault";
import { buildWebviewHtml } from "./webviewContent";
import { OverridesFile } from "./types";

/**
 * Everything this controller needs from the real VS Code extension host,
 * abstracted behind a plain interface so the actual message-handling
 * LOGIC (what happens when the webview says "clear all") can be unit
 * tested without a real vscode.Webview -- extension.ts implements this
 * interface with real vscode API calls; tests implement it with plain
 * in-memory fakes.
 */
export interface PanelHost {
  rootDir: string;
  cspSource: string;
  generateNonce(): string;
  postHtml(html: string): void;
  /** Show a modal confirmation before a destructive action, with
   * `message` describing exactly what will happen. Returns whether the
   * user confirmed. */
  confirmDestructive(message: string): Promise<boolean>;
}

export type WebviewMessage =
  | { command: "refresh" }
  | { command: "clearSession"; sessionId: string }
  | { command: "clearSessionDataOnly" }
  | { command: "clearAll" }
  | { command: "saveOverrides"; overrides: OverridesFile };

export const CLEAR_SESSION_DATA_ONLY_CONFIRM_MESSAGE =
  "Clear session data for EVERY session in this workspace? This removes the audit trail and " +
  "vault mappings for all sessions. Your configured always_mask/never_mask overrides are KEPT. " +
  "This cannot be undone.";

export const CLEAR_ALL_CONFIRM_MESSAGE =
  "Clear ALL Decoy local data? This removes the audit trail, vault mappings, AND your " +
  "configured always_mask/never_mask overrides for every session in this workspace -- " +
  "nothing is kept. This cannot be undone. To keep your overrides, use " +
  "\"Clear Session Data Only\" instead.";

export class PanelController {
  constructor(private readonly host: PanelHost) {}

  refresh(): void {
    const log = readAuditLog(this.host.rootDir);
    const groups = groupByRequest(log.entries);
    const overrides = readOverrides(this.host.rootDir);
    const html = buildWebviewHtml(groups, overrides, {
      cspSource: this.host.cspSource,
      nonce: this.host.generateNonce(),
    });
    this.host.postHtml(html);
  }

  async handleMessage(message: WebviewMessage): Promise<void> {
    switch (message.command) {
      case "refresh":
        this.refresh();
        return;

      case "clearSession":
        clearAuditSession(this.host.rootDir, message.sessionId);
        clearVaultSession(this.host.rootDir, message.sessionId);
        this.refresh();
        return;

      case "clearSessionDataOnly": {
        const confirmed = await this.host.confirmDestructive(CLEAR_SESSION_DATA_ONLY_CONFIRM_MESSAGE);
        if (confirmed) {
          clearAuditAll(this.host.rootDir);
          clearVaultAll(this.host.rootDir);
          this.refresh();
        }
        return;
      }

      case "clearAll": {
        const confirmed = await this.host.confirmDestructive(CLEAR_ALL_CONFIRM_MESSAGE);
        if (confirmed) {
          clearAuditAll(this.host.rootDir);
          clearVaultAll(this.host.rootDir);
          clearOverrides(this.host.rootDir);
          this.refresh();
        }
        return;
      }

      case "saveOverrides":
        writeOverrides(this.host.rootDir, message.overrides);
        this.refresh();
        return;
    }
  }
}

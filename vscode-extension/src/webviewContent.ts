import { AuditDecision, OverridesFile, RequestGroup } from "./types";

// Deliberately no absolute-privacy language anywhere in this UI copy --
// see the project's core design principles and Phase 11's forthcoming
// "what this protects against, and what it doesn't" doc, which this
// footer will link to once written.
const DISCLAIMER =
  "Decoy masks values it detects via regex/NER, manual overrides, and shape-based rules " +
  "before they reach an LLM. It does not guarantee protection against re-identification " +
  "from surrounding context, and automated relevance classification is imperfect -- manual " +
  "overrides exist precisely because of these limits. Nothing here ever shows a real or fake value.";

export function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// Icon/label pairing for each AuditDecision, using VS Code's Codicon font
// (the same icon set VS Code's own UI uses) rather than plain text labels.
// A separate "manual override" badge (warning triangle) is layered on top
// of these when an entry's `layer` is "override", since that's an
// orthogonal fact from the decision itself.
const DECISION_ICON: Record<AuditDecision, string> = {
  masked: "check",
  redacted: "eye-closed",
  left_as_is: "circle-large-outline",
};

const DECISION_CLASS: Record<AuditDecision, string> = {
  masked: "decision-masked",
  redacted: "decision-redacted",
  left_as_is: "decision-left",
};

function decisionLabel(decision: AuditDecision | string): string {
  if (decision === "left_as_is") return "left as-is";
  return decision;
}

function formatTimestamp(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function summarizeDecisions(group: RequestGroup): string {
  const counts: Record<string, number> = {};
  for (const e of group.entries) {
    counts[e.decision] = (counts[e.decision] ?? 0) + 1;
  }
  const parts: string[] = [];
  (["masked", "redacted", "left_as_is"] as AuditDecision[]).forEach((decision) => {
    const count = counts[decision];
    if (!count) return;
    parts.push(
      `<span class="summary-chip ${DECISION_CLASS[decision]}">` +
        `<span class="codicon codicon-${DECISION_ICON[decision]}"></span>${count}</span>`,
    );
  });
  return parts.join("");
}

function renderRequestGroup(group: RequestGroup, index: number): string {
  const rows = group.entries
    .map((e) => {
      const overrideBadge =
        e.layer === "override"
          ? `<span class="codicon codicon-warning override-flag" title="Changed by a manual override rule"></span>`
          : "";
      return `
      <tr>
        <td class="col-field">${escapeHtml(e.field)}</td>
        <td class="col-decision">
          <span class="badge ${DECISION_CLASS[e.decision]}">
            <span class="codicon codicon-${DECISION_ICON[e.decision] ?? "circle-large-outline"}"></span>
            ${escapeHtml(decisionLabel(e.decision))}
          </span>
          ${overrideBadge}
        </td>
        <td class="col-layer">${escapeHtml(e.layer)}</td>
        <td class="col-reason">${escapeHtml(e.reason)}</td>
      </tr>`;
    })
    .join("");

  // Only the most recent request starts expanded -- everything else is
  // collapsed by default so the panel reads as a scannable list of
  // headers, not a wall of tables.
  const openAttr = index === 0 ? " open" : "";

  return `
    <details class="request-group"${openAttr}>
      <summary>
        <span class="codicon codicon-chevron-right chevron"></span>
        <span class="request-summary">
          <span class="ts">${escapeHtml(formatTimestamp(group.timestamp))}</span>
          <span class="session" title="${escapeHtml(group.session_id)}">session ${escapeHtml(group.session_id.slice(0, 8))}</span>
          <span class="summary-chips">${summarizeDecisions(group)}</span>
        </span>
        <button class="icon-btn clear-session-btn" data-session-id="${escapeHtml(group.session_id)}" title="Clear this session's audit trail and vault mappings">
          <span class="codicon codicon-trash"></span>
        </button>
      </summary>
      <table>
        <thead><tr><th>Field</th><th>Decision</th><th>Layer</th><th>Reason</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </details>`;
}

function renderOverrideList(kind: "always_mask" | "never_mask", label: string, overrides: OverridesFile): string {
  const section = overrides[kind];
  const entries = [
    ...section.field_names.map((value) => ({ list: "field_names" as const, value, isPattern: false })),
    ...section.patterns.map((value) => ({ list: "patterns" as const, value, isPattern: true })),
  ];

  const rows = entries.length
    ? entries
        .map(
          (entry) => `
      <li class="override-row">
        <span class="override-kind-icon codicon codicon-${entry.isPattern ? "regex" : "symbol-field"}" title="${entry.isPattern ? "Regex pattern" : "Field name"}"></span>
        <span class="override-value">${entry.isPattern ? `<code>${escapeHtml(entry.value)}</code>` : escapeHtml(entry.value)}</span>
        <button class="icon-btn remove-override-btn" data-kind="${kind}" data-list="${entry.list}" data-value="${escapeHtml(entry.value)}" title="Remove this override">
          <span class="codicon codicon-close"></span>
        </button>
      </li>`,
        )
        .join("")
    : `<li class="override-empty">None yet.</li>`;

  return `
    <fieldset class="override-section">
      <legend>${escapeHtml(label)}</legend>
      <ul class="override-list">${rows}</ul>

      <div class="override-form">
        <div class="form-row">
          <label for="field-input-${kind}">Field name</label>
          <div class="form-input-group">
            <input type="text" id="field-input-${kind}" class="add-field-input" data-kind="${kind}" placeholder="field_name" />
            <button class="btn-secondary add-field-btn" data-kind="${kind}">
              <span class="codicon codicon-add"></span>Add
            </button>
          </div>
          <p class="field-error" id="field-error-${kind}" hidden></p>
        </div>
        <div class="form-row">
          <label for="pattern-input-${kind}">Pattern (regex)</label>
          <div class="form-input-group">
            <input type="text" id="pattern-input-${kind}" class="add-pattern-input" data-kind="${kind}" placeholder="e.g. EMP-\\d{6}" />
            <button class="btn-secondary add-pattern-btn" data-kind="${kind}">
              <span class="codicon codicon-add"></span>Add
            </button>
          </div>
          <p class="field-error" id="pattern-error-${kind}" hidden></p>
        </div>
      </div>
    </fieldset>`;
}

export interface ProxyStatusInfo {
  state: string; // "stopped" | "starting" | "running" | "stopping" | "error"
  detail?: string;
  port: number;
}

export interface ApprovalStatusInfo {
  status: string; // "pending" | "not_configured" | "approved" | "check_failed"
  message?: string;
}

export interface WebviewRenderOptions {
  cspSource: string;
  nonce: string;
  codiconsUri: string;
  requestCount: number;
  proxyStatus: ProxyStatusInfo;
  approvalStatus?: ApprovalStatusInfo;
}

const PROXY_STATE_ICON: Record<string, string> = {
  stopped: "circle-large-outline",
  starting: "loading",
  running: "pass-filled",
  stopping: "loading",
  error: "error",
};

function renderProxySection(status: ProxyStatusInfo): string {
  const icon = PROXY_STATE_ICON[status.state] ?? "circle-large-outline";
  const spinClass = status.state === "starting" || status.state === "stopping" ? " spin" : "";
  const label = status.state === "error" && status.detail ? `error: ${status.detail}` : status.state;
  const canStart = status.state === "stopped" || status.state === "error";
  const canStop = status.state === "running" || status.state === "starting";

  return `
    <fieldset class="override-section">
      <legend>Chat proxy</legend>
      <div class="proxy-row">
        <span class="codicon codicon-${icon}${spinClass} proxy-state-icon proxy-state-${escapeHtml(status.state)}"></span>
        <span class="proxy-state-label">${escapeHtml(label)}</span>
        <span class="proxy-port">port ${status.port}</span>
      </div>
      <div class="proxy-actions">
        <button id="start-proxy-btn" class="btn-secondary" ${canStart ? "" : "disabled"}>
          <span class="codicon codicon-play"></span>Start
        </button>
        <button id="stop-proxy-btn" class="btn-secondary" ${canStop ? "" : "disabled"}>
          <span class="codicon codicon-stop-circle"></span>Stop
        </button>
        <button id="restart-proxy-btn" class="btn-secondary">
          <span class="codicon codicon-sync"></span>Restart
        </button>
        <button id="set-api-key-btn" class="btn-secondary">
          <span class="codicon codicon-key"></span>Set API Key
        </button>
      </div>
      <p class="field-hint">Starting/stopping here replaces running <code>decoy chat-proxy</code> in a terminal yourself -- no terminal needs to stay open.</p>
    </fieldset>`;
}

function renderApprovalBanner(approval: ApprovalStatusInfo | undefined): string {
  if (!approval || approval.status !== "pending") {
    return "";
  }
  return `
    <div class="approval-banner" id="approval-banner">
      <span class="codicon codicon-warning"></span>
      <div class="approval-text">
        <strong>Action needed:</strong> ${escapeHtml(approval.message ?? "The Decoy MCP server is pending approval.")}
      </div>
    </div>`;
}

export function buildWebviewHtml(
  requests: RequestGroup[],
  overrides: OverridesFile,
  options: WebviewRenderOptions,
): string {
  const requestsHtml = requests.length
    ? requests.map(renderRequestGroup).join("")
    : `
      <div class="empty-state">
        <span class="codicon codicon-inbox empty-icon"></span>
        <p class="empty-title">No requests yet</p>
        <p class="empty-body">Once Claude Code sends a prompt through Decoy, you'll see what got masked here.</p>
      </div>`;

  const summaryLine =
    options.requestCount === 0
      ? "Watching for requests"
      : `${options.requestCount} request${options.requestCount === 1 ? "" : "s"} recorded`;

  return `<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${options.cspSource} 'unsafe-inline'; font-src ${options.cspSource}; script-src 'nonce-${options.nonce}';" />
  <link href="${options.codiconsUri}" rel="stylesheet" />
  <style>
    :root {
      --gap-xs: 4px;
      --gap-s: 8px;
      --gap-m: 12px;
      --radius: 4px;
    }
    * { box-sizing: border-box; }
    body {
      font-family: var(--vscode-font-family);
      font-size: var(--vscode-font-size, 13px);
      color: var(--vscode-foreground);
      background: var(--vscode-editor-background);
      padding: 0;
      margin: 0;
    }
    .panel { padding: var(--gap-s) var(--gap-m) var(--gap-m); }

    /* -------- header -------- */
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: var(--gap-s);
      padding: var(--gap-s) 0;
      border-bottom: 1px solid var(--vscode-panel-border);
      margin-bottom: var(--gap-m);
    }
    .header-title { display: flex; align-items: center; gap: var(--gap-s); min-width: 0; }
    .header-title .codicon { font-size: 16px; color: var(--vscode-charts-blue, var(--vscode-textLink-foreground)); }
    .header-text h1 {
      font-size: 12px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      margin: 0;
      color: var(--vscode-foreground);
    }
    .header-text .summary {
      display: flex;
      align-items: center;
      gap: 6px;
      font-size: 11px;
      color: var(--vscode-descriptionForeground);
      margin-top: 2px;
    }
    .live-dot {
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: var(--vscode-charts-green, #3fb950);
      display: inline-block;
      animation: pulse 2.4s ease-in-out infinite;
      flex-shrink: 0;
    }
    @keyframes pulse {
      0%, 100% { opacity: 1; }
      50% { opacity: 0.35; }
    }
    .toolbar { display: flex; gap: 2px; flex-shrink: 0; }

    /* -------- buttons -------- */
    button { cursor: pointer; font-family: inherit; }
    .icon-btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 24px;
      height: 24px;
      padding: 0;
      border: none;
      border-radius: var(--radius);
      background: transparent;
      color: var(--vscode-icon-foreground, var(--vscode-foreground));
    }
    .icon-btn:hover { background: var(--vscode-toolbar-hoverBackground, rgba(128,128,128,0.2)); }
    .icon-btn.danger:hover { color: var(--vscode-errorForeground, #f14c4c); }
    .btn-primary, .btn-secondary {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      border: 1px solid var(--vscode-button-border, transparent);
      border-radius: var(--radius);
      padding: 3px 10px;
      font-size: 12px;
    }
    .btn-primary {
      background: var(--vscode-button-background);
      color: var(--vscode-button-foreground);
    }
    .btn-primary:hover { background: var(--vscode-button-hoverBackground); }
    .btn-secondary {
      background: var(--vscode-button-secondaryBackground, transparent);
      color: var(--vscode-button-secondaryForeground, var(--vscode-foreground));
      border-color: var(--vscode-contrastBorder, var(--vscode-panel-border));
    }
    .btn-secondary:hover { background: var(--vscode-button-secondaryHoverBackground, rgba(128,128,128,0.2)); }
    .btn-danger {
      background: transparent;
      color: var(--vscode-errorForeground, #f14c4c);
      border-color: var(--vscode-errorForeground, #f14c4c);
    }
    .btn-danger:hover { background: rgba(241, 76, 76, 0.12); }

    /* -------- section headings -------- */
    section { margin-bottom: var(--gap-m); }
    h2 {
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: var(--vscode-descriptionForeground);
      margin: 0 0 var(--gap-s) 0;
    }

    /* -------- data actions row -------- */
    .data-actions { display: flex; gap: var(--gap-s); flex-wrap: wrap; margin-bottom: var(--gap-s); }
    .actions-hint { font-size: 11px; color: var(--vscode-descriptionForeground); margin: 0 0 var(--gap-m) 0; }

    /* -------- empty state -------- */
    .empty-state {
      display: flex;
      flex-direction: column;
      align-items: center;
      text-align: center;
      gap: 6px;
      padding: 28px 16px;
      color: var(--vscode-descriptionForeground);
      border: 1px dashed var(--vscode-panel-border);
      border-radius: var(--radius);
    }
    .empty-icon { font-size: 28px; opacity: 0.6; margin-bottom: 4px; }
    .empty-title { font-weight: 600; color: var(--vscode-foreground); margin: 0; }
    .empty-body { font-size: 12px; margin: 0; max-width: 32ch; }

    /* -------- request groups -------- */
    .request-group {
      border: 1px solid var(--vscode-panel-border);
      border-radius: var(--radius);
      margin-bottom: var(--gap-s);
      background: var(--vscode-sideBar-background, transparent);
    }
    .request-group summary {
      list-style: none;
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: var(--gap-s);
      padding: 6px 8px;
    }
    .request-group summary::-webkit-details-marker { display: none; }
    .chevron { transition: transform 0.1s ease; font-size: 14px; color: var(--vscode-descriptionForeground); flex-shrink: 0; }
    .request-group[open] > summary .chevron { transform: rotate(90deg); }
    .request-summary { display: flex; align-items: center; gap: var(--gap-s); flex-wrap: wrap; flex: 1; min-width: 0; }
    .ts { font-weight: 600; font-size: 12px; }
    .session {
      font-size: 11px;
      font-family: var(--vscode-editor-font-family, monospace);
      color: var(--vscode-descriptionForeground);
      background: var(--vscode-badge-background, rgba(128,128,128,0.2));
      padding: 1px 6px;
      border-radius: 10px;
    }
    .summary-chips { display: flex; gap: 4px; }
    .summary-chip {
      display: inline-flex;
      align-items: center;
      gap: 3px;
      font-size: 11px;
      padding: 1px 5px;
      border-radius: 8px;
    }
    .summary-chip .codicon { font-size: 12px; }

    table { width: 100%; border-collapse: collapse; font-size: 12px; border-top: 1px solid var(--vscode-panel-border); }
    th, td { text-align: left; padding: 4px 8px; border-bottom: 1px solid var(--vscode-panel-border); vertical-align: top; }
    th { color: var(--vscode-descriptionForeground); font-weight: 600; font-size: 11px; }
    .col-field { font-family: var(--vscode-editor-font-family, monospace); white-space: nowrap; }
    .col-reason { color: var(--vscode-descriptionForeground); }

    .badge {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 1px 7px;
      border-radius: 8px;
      font-size: 11px;
      white-space: nowrap;
    }
    .badge .codicon { font-size: 12px; }
    .decision-masked { background: color-mix(in srgb, var(--vscode-charts-blue, #3794ff) 22%, transparent); color: var(--vscode-charts-blue, #3794ff); }
    .decision-redacted { background: color-mix(in srgb, var(--vscode-charts-red, #f14c4c) 22%, transparent); color: var(--vscode-charts-red, #f14c4c); }
    .decision-left { background: color-mix(in srgb, var(--vscode-charts-green, #89d185) 22%, transparent); color: var(--vscode-charts-green, #89d185); }
    .override-flag { font-size: 12px; color: var(--vscode-charts-orange, #cca700); margin-left: 4px; vertical-align: middle; }

    /* -------- overrides form -------- */
    .override-section {
      border: 1px solid var(--vscode-panel-border);
      border-radius: var(--radius);
      padding: var(--gap-s) var(--gap-m) var(--gap-m);
      margin: 0 0 var(--gap-s) 0;
    }
    .override-section legend {
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: var(--vscode-descriptionForeground);
      padding: 0 4px;
    }
    .override-list { list-style: none; padding: 0; margin: 0 0 var(--gap-s) 0; }
    .override-row {
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 3px 4px;
      border-radius: var(--radius);
    }
    .override-row:hover { background: var(--vscode-list-hoverBackground); }
    .override-kind-icon { font-size: 13px; color: var(--vscode-descriptionForeground); flex-shrink: 0; }
    .override-value { flex: 1; min-width: 0; overflow-wrap: anywhere; font-size: 12px; }
    .override-empty { font-size: 12px; color: var(--vscode-descriptionForeground); font-style: italic; padding: 3px 4px; }

    .override-form { display: flex; flex-direction: column; gap: var(--gap-s); }
    .form-row { display: flex; flex-direction: column; gap: 3px; }
    .form-row label { font-size: 11px; color: var(--vscode-descriptionForeground); }
    .form-input-group { display: flex; gap: 6px; }
    .form-input-group input {
      flex: 1;
      min-width: 0;
      background: var(--vscode-input-background);
      color: var(--vscode-input-foreground);
      border: 1px solid var(--vscode-input-border, var(--vscode-panel-border));
      border-radius: var(--radius);
      padding: 3px 6px;
      font-size: 12px;
      font-family: var(--vscode-font-family);
    }
    .form-input-group input:focus {
      outline: 1px solid var(--vscode-focusBorder);
      outline-offset: -1px;
    }
    .form-input-group input.invalid { border-color: var(--vscode-errorForeground, #f14c4c); }
    .field-error {
      font-size: 11px;
      color: var(--vscode-errorForeground, #f14c4c);
      margin: 0;
    }
    .field-hint {
      font-size: 11px;
      color: var(--vscode-descriptionForeground);
      margin: var(--gap-s) 0 0;
    }

    /* -------- chat proxy lifecycle controls -------- */
    .proxy-row { display: flex; align-items: center; gap: 6px; margin-bottom: var(--gap-s); }
    .proxy-state-icon { font-size: 14px; }
    .proxy-state-running { color: var(--vscode-charts-green, #89d185); }
    .proxy-state-error { color: var(--vscode-errorForeground, #f14c4c); }
    .proxy-state-stopped, .proxy-state-stopping { color: var(--vscode-descriptionForeground); }
    .proxy-state-label { font-size: 12px; font-weight: 600; text-transform: capitalize; }
    .proxy-port { font-size: 11px; color: var(--vscode-descriptionForeground); margin-left: auto; font-family: var(--vscode-editor-font-family, monospace); }
    .proxy-actions { display: flex; gap: 6px; flex-wrap: wrap; }

    /* -------- approval banner -------- */
    .approval-banner {
      display: flex;
      align-items: flex-start;
      gap: 8px;
      background: color-mix(in srgb, var(--vscode-charts-orange, #cca700) 16%, transparent);
      border: 1px solid var(--vscode-charts-orange, #cca700);
      border-radius: var(--radius);
      padding: var(--gap-s) var(--gap-m);
      margin-bottom: var(--gap-m);
      font-size: 12px;
    }
    .approval-banner .codicon { color: var(--vscode-charts-orange, #cca700); font-size: 16px; margin-top: 1px; }
    .approval-text { line-height: 1.4; }

    /* -------- pending / loading overlay -------- */
    .pending-banner {
      display: none;
      align-items: center;
      gap: 6px;
      font-size: 11px;
      color: var(--vscode-descriptionForeground);
      padding: 4px 0;
    }
    .pending-banner.visible { display: flex; }
    .spin { animation: spin 1s linear infinite; }
    @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }

    .disclaimer {
      font-size: 11px;
      color: var(--vscode-descriptionForeground);
      border-top: 1px solid var(--vscode-panel-border);
      padding-top: var(--gap-s);
      margin-top: var(--gap-m);
    }
  </style>
</head>
<body>
  <div class="panel">
    <header>
      <div class="header-title">
        <span class="codicon codicon-shield"></span>
        <div class="header-text">
          <h1>Decoy</h1>
          <div class="summary"><span class="live-dot" title="Live: this panel updates automatically as requests land"></span>${escapeHtml(summaryLine)}</div>
        </div>
      </div>
      <div class="toolbar">
        <button id="refresh-btn" class="icon-btn" title="Refresh">
          <span class="codicon codicon-refresh"></span>
        </button>
        <button id="clear-session-data-btn" class="icon-btn danger" title="Clear Session Data Only -- removes the audit trail and vault mappings for every session. Keeps your configured overrides.">
          <span class="codicon codicon-clear-all"></span>
        </button>
        <button id="clear-all-btn" class="icon-btn danger" title="Clear All Local Data -- removes EVERYTHING, including your configured overrides.">
          <span class="codicon codicon-trash"></span>
        </button>
      </div>
    </header>

    <p class="pending-banner" id="pending-banner">
      <span class="codicon codicon-loading spin"></span>
      Updating&hellip;
    </p>

    <p class="actions-hint">
      "Clear Session Data Only" keeps your always_mask/never_mask overrides. "Clear All Local Data"
      removes those too, with nothing kept back.
    </p>

    ${renderApprovalBanner(options.approvalStatus)}

    <section>
      ${renderProxySection(options.proxyStatus)}
    </section>

    <section>
      <h2>Recent requests</h2>
      <div id="requests">${requestsHtml}</div>
    </section>

    <section>
      <h2>Overrides</h2>
      <div id="overrides">
        ${renderOverrideList("always_mask", "Always mask", overrides)}
        ${renderOverrideList("never_mask", "Never mask", overrides)}
      </div>
    </section>

    <div class="disclaimer">${escapeHtml(DISCLAIMER)}</div>
  </div>

  <script nonce="${options.nonce}">
    const vscode = acquireVsCodeApi();
    const pendingBanner = document.getElementById("pending-banner");

    // Shows the "Updating..." indicator for the gap between an action
    // being sent to the extension host and the refreshed HTML landing --
    // this HTML is about to be replaced wholesale by postHtml(), so this
    // only needs to survive until then, not manage its own hide logic.
    function showPending() {
      pendingBanner.classList.add("visible");
    }

    function post(message) {
      showPending();
      vscode.postMessage(message);
    }

    document.getElementById("refresh-btn").addEventListener("click", () => {
      post({ command: "refresh" });
    });

    document.getElementById("clear-session-data-btn").addEventListener("click", () => {
      post({ command: "clearSessionDataOnly" });
    });

    document.getElementById("clear-all-btn").addEventListener("click", () => {
      post({ command: "clearAll" });
    });

    const startProxyBtn = document.getElementById("start-proxy-btn");
    if (startProxyBtn) startProxyBtn.addEventListener("click", () => post({ command: "startChatProxy" }));
    const stopProxyBtn = document.getElementById("stop-proxy-btn");
    if (stopProxyBtn) stopProxyBtn.addEventListener("click", () => post({ command: "stopChatProxy" }));
    const restartProxyBtn = document.getElementById("restart-proxy-btn");
    if (restartProxyBtn) restartProxyBtn.addEventListener("click", () => post({ command: "restartChatProxy" }));
    const setApiKeyBtn = document.getElementById("set-api-key-btn");
    if (setApiKeyBtn) setApiKeyBtn.addEventListener("click", () => post({ command: "setApiKey" }));

    document.querySelectorAll(".clear-session-btn").forEach((btn) => {
      btn.addEventListener("click", (ev) => {
        ev.preventDefault();
        const sessionId = btn.getAttribute("data-session-id");
        post({ command: "clearSession", sessionId });
      });
    });

    let pendingOverrides = ${JSON.stringify(overrides)};

    document.querySelectorAll(".remove-override-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const kind = btn.getAttribute("data-kind");
        const list = btn.getAttribute("data-list");
        const value = btn.getAttribute("data-value");
        pendingOverrides[kind][list] = pendingOverrides[kind][list].filter((v) => v !== value);
        post({ command: "saveOverrides", overrides: pendingOverrides });
      });
    });

    function showFieldError(id, message) {
      const el = document.getElementById(id);
      if (!el) return;
      if (message) {
        el.textContent = message;
        el.hidden = false;
      } else {
        el.textContent = "";
        el.hidden = true;
      }
    }

    document.querySelectorAll(".add-field-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const kind = btn.getAttribute("data-kind");
        const input = document.querySelector('.add-field-input[data-kind="' + kind + '"]');
        const value = input.value.trim();
        showFieldError("field-error-" + kind, null);
        input.classList.remove("invalid");
        if (!value) {
          input.classList.add("invalid");
          showFieldError("field-error-" + kind, "Field name can't be empty.");
          return;
        }
        pendingOverrides[kind].field_names.push(value);
        input.value = "";
        post({ command: "saveOverrides", overrides: pendingOverrides });
      });
    });

    document.querySelectorAll(".add-pattern-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const kind = btn.getAttribute("data-kind");
        const input = document.querySelector('.add-pattern-input[data-kind="' + kind + '"]');
        const value = input.value.trim();
        showFieldError("pattern-error-" + kind, null);
        input.classList.remove("invalid");
        if (!value) {
          input.classList.add("invalid");
          showFieldError("pattern-error-" + kind, "Pattern can't be empty.");
          return;
        }
        try {
          new RegExp(value);
        } catch (err) {
          input.classList.add("invalid");
          showFieldError("pattern-error-" + kind, "Not a valid regular expression: " + err.message);
          return;
        }
        pendingOverrides[kind].patterns.push(value);
        input.value = "";
        post({ command: "saveOverrides", overrides: pendingOverrides });
      });
    });
  </script>
</body>
</html>`;
}

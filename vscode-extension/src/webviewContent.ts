import { OverridesFile, RequestGroup } from "./types";

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

function decisionBadgeClass(decision: string): string {
  if (decision === "masked") return "badge-masked";
  if (decision === "redacted") return "badge-redacted";
  if (decision === "left_as_is") return "badge-left";
  return "badge-unknown";
}

function renderRequestGroup(group: RequestGroup): string {
  const rows = group.entries
    .map(
      (e) => `
      <tr>
        <td>${escapeHtml(e.field)}</td>
        <td><span class="badge ${decisionBadgeClass(e.decision)}">${escapeHtml(e.decision)}</span></td>
        <td>${escapeHtml(e.layer)}</td>
        <td class="reason">${escapeHtml(e.reason)}</td>
      </tr>`,
    )
    .join("");

  return `
    <details class="request-group">
      <summary>
        <span class="ts">${escapeHtml(group.timestamp)}</span>
        <span class="session">session: ${escapeHtml(group.session_id)}</span>
        <span class="count">${group.entries.length} decision(s)</span>
        <button class="clear-session-btn" data-session-id="${escapeHtml(group.session_id)}">
          Clear this session
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
  const fieldItems = section.field_names
    .map(
      (f) => `
      <li>
        <span>${escapeHtml(f)}</span>
        <button class="remove-override-btn" data-kind="${kind}" data-list="field_names" data-value="${escapeHtml(f)}">x</button>
      </li>`,
    )
    .join("");
  const patternItems = section.patterns
    .map(
      (p) => `
      <li>
        <span><code>${escapeHtml(p)}</code></span>
        <button class="remove-override-btn" data-kind="${kind}" data-list="patterns" data-value="${escapeHtml(p)}">x</button>
      </li>`,
    )
    .join("");

  return `
    <div class="override-section">
      <h4>${escapeHtml(label)}</h4>
      <div class="override-subsection">
        <strong>Field names</strong>
        <ul>${fieldItems}</ul>
        <div class="add-row">
          <input type="text" class="add-field-input" data-kind="${kind}" placeholder="field_name" />
          <button class="add-field-btn" data-kind="${kind}">Add</button>
        </div>
      </div>
      <div class="override-subsection">
        <strong>Patterns (regex)</strong>
        <ul>${patternItems}</ul>
        <div class="add-row">
          <input type="text" class="add-pattern-input" data-kind="${kind}" placeholder="regex pattern" />
          <button class="add-pattern-btn" data-kind="${kind}">Add</button>
        </div>
      </div>
    </div>`;
}

export interface WebviewRenderOptions {
  cspSource: string;
  nonce: string;
}

export function buildWebviewHtml(
  requests: RequestGroup[],
  overrides: OverridesFile,
  options: WebviewRenderOptions,
): string {
  const requestsHtml = requests.length
    ? requests.map(renderRequestGroup).join("")
    : `<p class="empty">No requests recorded yet in this workspace.</p>`;

  return `<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${options.cspSource} 'unsafe-inline'; script-src 'nonce-${options.nonce}';" />
  <style>
    body { font-family: var(--vscode-font-family); color: var(--vscode-foreground); padding: 8px; }
    h3, h4 { margin-bottom: 4px; }
    .disclaimer { font-size: 11px; opacity: 0.8; border-top: 1px solid var(--vscode-panel-border); padding-top: 6px; margin-top: 12px; }
    .request-group { margin-bottom: 8px; border: 1px solid var(--vscode-panel-border); border-radius: 4px; padding: 4px 8px; }
    .request-group summary { cursor: pointer; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    table { width: 100%; border-collapse: collapse; margin-top: 6px; font-size: 12px; }
    th, td { text-align: left; padding: 2px 6px; border-bottom: 1px solid var(--vscode-panel-border); }
    .reason { max-width: 320px; }
    .badge { padding: 1px 6px; border-radius: 8px; font-size: 11px; }
    .badge-masked { background: var(--vscode-charts-blue, #3794ff); color: white; }
    .badge-redacted { background: var(--vscode-charts-red, #f14c4c); color: white; }
    .badge-left { background: var(--vscode-charts-green, #89d185); color: black; }
    .override-section { margin-bottom: 12px; }
    .override-section ul { list-style: none; padding-left: 0; }
    .override-section li { display: flex; justify-content: space-between; align-items: center; padding: 2px 0; }
    .add-row { display: flex; gap: 4px; margin-top: 4px; }
    .add-row input { flex: 1; }
    .actions { display: flex; gap: 8px; margin: 8px 0; flex-wrap: wrap; }
    .actions-hint { font-size: 11px; opacity: 0.75; margin: 0 0 12px 0; }
    button.danger { border-color: var(--vscode-charts-red, #f14c4c); }
    button { cursor: pointer; }
    .empty { opacity: 0.7; font-style: italic; }
  </style>
</head>
<body>
  <div class="actions">
    <button id="refresh-btn">Refresh</button>
    <button id="clear-session-data-btn" title="Removes the audit trail and vault mappings for every session. Keeps your configured overrides.">
      Clear Session Data Only
    </button>
    <button id="clear-all-btn" class="danger" title="Removes EVERYTHING: audit trail, vault mappings, AND your configured overrides.">
      Clear All Local Data (incl. overrides)
    </button>
  </div>
  <p class="actions-hint">
    "Clear Session Data Only" keeps your always_mask/never_mask overrides. "Clear All Local Data"
    removes those too, with nothing kept back.
  </p>

  <h3>Recent requests</h3>
  <div id="requests">${requestsHtml}</div>

  <h3>Overrides</h3>
  <div id="overrides">
    ${renderOverrideList("always_mask", "Always mask", overrides)}
    ${renderOverrideList("never_mask", "Never mask", overrides)}
    <button id="save-overrides-btn">Save Overrides</button>
  </div>

  <div class="disclaimer">${escapeHtml(DISCLAIMER)}</div>

  <script nonce="${options.nonce}">
    const vscode = acquireVsCodeApi();

    document.getElementById("refresh-btn").addEventListener("click", () => {
      vscode.postMessage({ command: "refresh" });
    });

    document.getElementById("clear-session-data-btn").addEventListener("click", () => {
      vscode.postMessage({ command: "clearSessionDataOnly" });
    });

    document.getElementById("clear-all-btn").addEventListener("click", () => {
      vscode.postMessage({ command: "clearAll" });
    });

    document.querySelectorAll(".clear-session-btn").forEach((btn) => {
      btn.addEventListener("click", (ev) => {
        ev.preventDefault();
        const sessionId = btn.getAttribute("data-session-id");
        vscode.postMessage({ command: "clearSession", sessionId });
      });
    });

    let pendingOverrides = ${JSON.stringify(overrides)};

    document.querySelectorAll(".remove-override-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const kind = btn.getAttribute("data-kind");
        const list = btn.getAttribute("data-list");
        const value = btn.getAttribute("data-value");
        pendingOverrides[kind][list] = pendingOverrides[kind][list].filter((v) => v !== value);
        vscode.postMessage({ command: "saveOverrides", overrides: pendingOverrides });
      });
    });

    document.querySelectorAll(".add-field-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const kind = btn.getAttribute("data-kind");
        const input = document.querySelector('.add-field-input[data-kind="' + kind + '"]');
        const value = input.value.trim();
        if (!value) return;
        pendingOverrides[kind].field_names.push(value);
        vscode.postMessage({ command: "saveOverrides", overrides: pendingOverrides });
      });
    });

    document.querySelectorAll(".add-pattern-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const kind = btn.getAttribute("data-kind");
        const input = document.querySelector('.add-pattern-input[data-kind="' + kind + '"]');
        const value = input.value.trim();
        if (!value) return;
        pendingOverrides[kind].patterns.push(value);
        vscode.postMessage({ command: "saveOverrides", overrides: pendingOverrides });
      });
    });

    document.getElementById("save-overrides-btn").addEventListener("click", () => {
      vscode.postMessage({ command: "saveOverrides", overrides: pendingOverrides });
    });
  </script>
</body>
</html>`;
}

import { buildWebviewHtml, escapeHtml } from "./webviewContent";
import { emptyOverridesFile, OverridesFile, RequestGroup } from "./types";

const OPTIONS = { cspSource: "vscode-webview://abc", nonce: "test-nonce" };

function makeGroup(overrides: Partial<RequestGroup> = {}): RequestGroup {
  return {
    request_id: "req-1",
    session_id: "session-1",
    timestamp: "2026-08-30T21:15:03.482Z",
    entries: [
      {
        id: "e1",
        request_id: "req-1",
        session_id: "session-1",
        timestamp: "2026-08-30T21:15:03.482Z",
        source: "prompt_text",
        field: "EMAIL",
        decision: "masked",
        reason: "matched EMAIL regex",
        layer: "regex",
      },
    ],
    ...overrides,
  };
}

describe("escapeHtml", () => {
  it("escapes all five special characters", () => {
    expect(escapeHtml(`<script>&"'</script>`)).toBe("&lt;script&gt;&amp;&quot;&#39;&lt;/script&gt;");
  });
});

describe("buildWebviewHtml", () => {
  it("renders field/decision/reason/layer for each entry", () => {
    const html = buildWebviewHtml([makeGroup()], emptyOverridesFile(), OPTIONS);
    expect(html).toContain("EMAIL");
    expect(html).toContain("masked");
    expect(html).toContain("matched EMAIL regex");
    expect(html).toContain("regex");
    expect(html).toContain("session-1");
  });

  it("shows an empty state when there are no requests", () => {
    const html = buildWebviewHtml([], emptyOverridesFile(), OPTIONS);
    expect(html).toContain("No requests recorded yet");
  });

  it("includes the no-absolute-privacy disclaimer text", () => {
    const html = buildWebviewHtml([], emptyOverridesFile(), OPTIONS);
    expect(html.toLowerCase()).not.toContain("100% private");
    expect(html.toLowerCase()).not.toContain("never leaves your machine");
    expect(html).toContain("does not guarantee protection against");
    expect(html).toContain("automated relevance classification is imperfect");
  });

  it("renders the CSP nonce and never allows a script-src wildcard", () => {
    const html = buildWebviewHtml([], emptyOverridesFile(), OPTIONS);
    expect(html).toContain(`script-src 'nonce-${OPTIONS.nonce}'`);
    expect(html).not.toContain("script-src *");
  });

  it("HTML-escapes untrusted content from entries so it cannot break out of markup", () => {
    const malicious = makeGroup({
      entries: [
        {
          id: "e1",
          request_id: "req-1",
          session_id: "session-1",
          timestamp: "2026-08-30T21:15:03.482Z",
          source: "db_record",
          field: `</td><script>alert(1)</script>`,
          decision: "masked",
          reason: `"><img src=x onerror=alert(1)>`,
          layer: "regex",
        },
      ],
    });
    const html = buildWebviewHtml([malicious], emptyOverridesFile(), OPTIONS);
    expect(html).not.toContain("<script>alert(1)</script>");
    expect(html).not.toContain("<img src=x onerror=alert(1)>");
  });

  it("renders configured overrides for both always_mask and never_mask", () => {
    const overrides: OverridesFile = {
      always_mask: { patterns: ["EMP-\\d{6}"], field_names: ["employee_id"] },
      never_mask: { patterns: [], field_names: ["status"] },
    };
    const html = buildWebviewHtml([], overrides, OPTIONS);
    expect(html).toContain("employee_id");
    expect(html).toContain("EMP-\\d{6}");
    expect(html).toContain("status");
  });

  it("never renders anything resembling a real or fake value field -- only known metadata keys", () => {
    // Structural guard: the HTML template has no interpolation point for
    // "original" or "fake" at all, so this test would fail loudly if
    // someone later added one by mistake.
    const html = buildWebviewHtml([makeGroup()], emptyOverridesFile(), OPTIONS);
    expect(html).not.toMatch(/original|fake_value|realValue/i);
  });

  it("has two distinctly-labeled clear buttons with no unstated exception on 'Clear All'", () => {
    const html = buildWebviewHtml([], emptyOverridesFile(), OPTIONS);
    // "Clear Session Data Only" exists and posts the session-only command
    expect(html).toContain("Clear Session Data Only");
    expect(html).toContain('command: "clearSessionDataOnly"');
    // "Clear All Local Data" is explicitly labeled as including overrides
    expect(html).toContain("Clear All Local Data");
    expect(html.toLowerCase()).toContain("incl. overrides");
    expect(html).toContain('command: "clearAll"');
    // the hint text spells out the difference so neither label is a surprise
    expect(html).toContain("keeps your always_mask/never_mask overrides");
  });
});

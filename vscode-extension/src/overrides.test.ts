import * as fs from "fs";
import * as os from "os";
import * as path from "path";

import { clearOverrides, readOverrides, writeOverrides } from "./overrides";

function makeTmpDir(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), "decoy-overrides-test-"));
}

describe("readOverrides", () => {
  it("returns empty rules when no file exists", () => {
    const root = makeTmpDir();
    const result = readOverrides(root);
    expect(result).toEqual({
      always_mask: { patterns: [], field_names: [] },
      never_mask: { patterns: [], field_names: [] },
    });
  });

  it("reads a well-formed file written by the Python side's format", () => {
    const root = makeTmpDir();
    fs.mkdirSync(path.join(root, ".decoy"));
    fs.writeFileSync(
      path.join(root, ".decoy", "overrides.json"),
      JSON.stringify({
        always_mask: { patterns: ["EMP-\\d{6}"], field_names: ["employee_id"] },
        never_mask: { patterns: [], field_names: ["id"] },
      }),
    );
    const result = readOverrides(root);
    expect(result.always_mask.patterns).toEqual(["EMP-\\d{6}"]);
    expect(result.always_mask.field_names).toEqual(["employee_id"]);
    expect(result.never_mask.field_names).toEqual(["id"]);
  });

  it("fails safe to empty rules on malformed JSON", () => {
    const root = makeTmpDir();
    fs.mkdirSync(path.join(root, ".decoy"));
    fs.writeFileSync(path.join(root, ".decoy", "overrides.json"), "{ not valid json");
    const result = readOverrides(root);
    expect(result.always_mask.field_names).toEqual([]);
  });

  it("ignores non-string entries rather than crashing", () => {
    const root = makeTmpDir();
    fs.mkdirSync(path.join(root, ".decoy"));
    fs.writeFileSync(
      path.join(root, ".decoy", "overrides.json"),
      JSON.stringify({ always_mask: { field_names: ["ok", 123, null] }, never_mask: {} }),
    );
    const result = readOverrides(root);
    expect(result.always_mask.field_names).toEqual(["ok"]);
  });
});

describe("writeOverrides", () => {
  it("round-trips through readOverrides", () => {
    const root = makeTmpDir();
    const data = {
      always_mask: { patterns: ["FOO-\\d+"], field_names: ["employee_id"] },
      never_mask: { patterns: [], field_names: ["status"] },
    };
    writeOverrides(root, data);
    expect(readOverrides(root)).toEqual(data);
  });

  it("writes valid JSON directly parseable (no atomic-write artifacts left behind)", () => {
    const root = makeTmpDir();
    writeOverrides(root, { always_mask: { patterns: [], field_names: ["x"] }, never_mask: { patterns: [], field_names: [] } });
    const finalPath = path.join(root, ".decoy", "overrides.json");
    const tmpPath = finalPath + ".tmp";
    expect(fs.existsSync(finalPath)).toBe(true);
    expect(fs.existsSync(tmpPath)).toBe(false);
    expect(() => JSON.parse(fs.readFileSync(finalPath, "utf8"))).not.toThrow();
  });
});

describe("clearOverrides", () => {
  it("resets an existing overrides file to empty", () => {
    const root = makeTmpDir();
    writeOverrides(root, {
      always_mask: { patterns: ["EMP-\\d{6}"], field_names: ["employee_id"] },
      never_mask: { patterns: [], field_names: ["status"] },
    });

    clearOverrides(root);

    expect(readOverrides(root)).toEqual({
      always_mask: { patterns: [], field_names: [] },
      never_mask: { patterns: [], field_names: [] },
    });
  });

  it("does nothing if no overrides file exists yet (not an error)", () => {
    const root = makeTmpDir();
    expect(() => clearOverrides(root)).not.toThrow();
    expect(fs.existsSync(path.join(root, ".decoy", "overrides.json"))).toBe(false);
  });
});

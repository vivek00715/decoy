#!/usr/bin/env node
/**
 * Copies PyInstaller-built `decoy-proxy` binaries into this extension's
 * own `bin/` directory so `vsce package` includes them in the .vsix.
 *
 * Packaging approach chosen: ONE universal .vsix bundling all three
 * platforms' binaries (~34MB each, ~100MB total), NOT vsce's
 * platform-specific `--target` builds (one slim .vsix per OS/arch).
 *
 * Why: this environment has no way to actually run `vsce package
 * --target <target>` end-to-end and verify the result installs
 * correctly on each target -- there's no real VSIX-install test
 * available here. A single universal package is the simpler thing to
 * get right blind: one `vsce package` invocation, one artifact, no
 * per-target publish matrix to keep in sync with
 * .github/workflows/release-binaries.yml's three build jobs, and no
 * risk of accidentally shipping a target-specific vsix with the WRONG
 * platform's binary inside it (a mistake that's easy to make and hard
 * to catch without installing on each real OS). ~100MB total is well
 * under the Marketplace's per-extension size limit. If this ever needs
 * to shrink, switching to `--target` builds later is a mechanical
 * change to this script (only copy the current platform's binary) plus
 * the packaging command in CI -- the runtime lookup logic below
 * (findBundledBinary in mcpConfigCommand.ts) already keys off
 * platform/arch, so it works unmodified either way.
 *
 * Naming convention this script writes to and mcpConfigCommand.ts reads
 * from: bin/<platform>-<arch>/decoy-proxy(.exe), where <platform> is
 * Node's `process.platform` value (darwin/win32/linux) and <arch> is
 * `process.arch` (x64/arm64) -- i.e. exactly what a bundled binary
 * needs to be looked up by at runtime via the same two Node globals.
 *
 * This script only COPIES whatever's already built; it never invokes
 * PyInstaller itself. In this environment, only a macOS binary could
 * actually be built (see ../../scripts/build_binary.py) -- the other
 * two platforms' binaries are produced by
 * .github/workflows/release-binaries.yml's CI matrix and are expected
 * to be dropped into the same ../dist-<platform>-<arch>/ style
 * locations (or passed via SOURCE_DIST_DIR below) before this script
 * runs as part of a real release build.
 */

const fs = require("fs");
const path = require("path");

const REPO_ROOT = path.resolve(__dirname, "..", "..");
const EXTENSION_ROOT = path.resolve(__dirname, "..");
const BIN_DIR = path.join(EXTENSION_ROOT, "bin");

// Maps a Node (platform, arch) pair to the binary this script expects to
// find already built. `scripts/build_binary.py` (run per-OS, since
// PyInstaller cross-compilation isn't a thing) always names its output
// dist/decoy-proxy(.exe) for the CURRENT machine's platform/arch -- it
// doesn't itself produce a platform-suffixed name. When building on CI's
// three-OS matrix (release-binaries.yml), each job's dist/decoy-proxy
// belongs to that job's own platform/arch, so this script looks for it
// under a directory the caller points at via SOURCE_DIST_DIR (falling
// back to the repo's plain `dist/` for a local single-platform build,
// which is what this environment can actually produce and test).
const TARGETS = [
  { platform: "darwin", arch: "x64", binaryName: "decoy-proxy" },
  { platform: "darwin", arch: "arm64", binaryName: "decoy-proxy" },
  { platform: "win32", arch: "x64", binaryName: "decoy-proxy.exe" },
  { platform: "linux", arch: "x64", binaryName: "decoy-proxy" },
];

function candidateSourcePaths(target) {
  const distDirEnv = process.env.SOURCE_DIST_DIR;
  const candidates = [];
  if (distDirEnv) {
    candidates.push(path.join(distDirEnv, target.binaryName));
  }
  // A local single-platform build (this environment's case): plain
  // repo-root dist/decoy-proxy, valid only for the CURRENT machine's
  // own platform/arch -- never assume it matches a different target.
  if (target.platform === process.platform && target.arch === process.arch) {
    candidates.push(path.join(REPO_ROOT, "dist", target.binaryName));
  }
  // CI's expected per-target output layout, e.g.
  // dist-darwin-x64/decoy-proxy -- release-binaries.yml is expected to
  // stage each matrix job's output here before invoking this script.
  candidates.push(path.join(REPO_ROOT, `dist-${target.platform}-${target.arch}`, target.binaryName));
  return candidates;
}

function main() {
  fs.mkdirSync(BIN_DIR, { recursive: true });

  let copied = 0;
  for (const target of TARGETS) {
    const destDir = path.join(BIN_DIR, `${target.platform}-${target.arch}`);
    const destPath = path.join(destDir, target.binaryName);

    const source = candidateSourcePaths(target).find((p) => fs.existsSync(p));
    if (!source) {
      console.log(`[bundle-binaries] no built binary found for ${target.platform}-${target.arch}, skipping`);
      continue;
    }

    fs.mkdirSync(destDir, { recursive: true });
    fs.copyFileSync(source, destPath);
    if (target.platform !== "win32") {
      fs.chmodSync(destPath, 0o755);
    }
    console.log(`[bundle-binaries] copied ${source} -> ${destPath}`);
    copied += 1;
  }

  if (copied === 0) {
    console.warn(
      "[bundle-binaries] WARNING: no platform binaries were found/copied. " +
        "Run scripts/build_binary.py at the repo root first (see its own docstring), " +
        "or set SOURCE_DIST_DIR to point at a directory containing decoy-proxy(.exe).",
    );
  }
}

main();

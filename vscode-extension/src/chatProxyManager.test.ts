import * as http from "http";
import * as path from "path";

import { ChatProxyManager, ProxyState } from "./chatProxyManager";

/**
 * Uses a REAL `python -m decoy.cli chat-proxy` process, not a mock --
 * standing in for the bundled binary the real extension will spawn. The
 * point of this test is proving the manual "open a terminal and run
 * decoy chat-proxy yourself" step is actually gone, which a mocked
 * child_process cannot demonstrate.
 */
const PYTHON = process.env.DECOY_TEST_PYTHON || "python3";
const REPO_ROOT = path.resolve(__dirname, "..", "..");
const TEST_PORT = 8834;

function waitForState(manager: ChatProxyManager, target: ProxyState, timeoutMs = 15000): Promise<void> {
  return new Promise((resolve, reject) => {
    if (manager.getState() === target) {
      resolve();
      return;
    }
    const timer = setTimeout(() => {
      clearInterval(check);
      reject(new Error(`timed out waiting for state ${target}, got ${manager.getState()}: ${manager.getLastError()}`));
    }, timeoutMs);
    const check = setInterval(() => {
      if (manager.getState() === target) {
        clearInterval(check);
        clearTimeout(timer);
        resolve();
      }
    }, 100);
  });
}

function httpGetOnce(url: string): Promise<{ status: number; body: string }> {
  return new Promise((resolve, reject) => {
    const req = http.get(url, (res) => {
      let body = "";
      res.on("data", (chunk) => (body += chunk));
      res.on("end", () => resolve({ status: res.statusCode ?? 0, body }));
    });
    req.on("error", reject);
    req.setTimeout(5000, () => req.destroy(new Error("request timed out")));
  });
}

/** ChatProxyManager's "running" state means the process was spawned
 * successfully (Node's 'spawn' event) -- it does NOT mean uvicorn has
 * finished importing decoy, building the app, and binding the port yet.
 * Confirmed directly: an immediate GET right after "running" raced
 * uvicorn's own startup and got ECONNREFUSED. Retrying is the correct
 * fix here, not a longer fixed sleep -- this is a real gap between
 * "process exists" and "socket accepts connections" inherent to any
 * subprocess-based server, not something ChatProxyManager itself is
 * expected to close (that would require the manager to speak HTTP,
 * which isn't its job). */
async function httpGet(url: string, timeoutMs = 10000): Promise<{ status: number; body: string }> {
  const deadline = Date.now() + timeoutMs;
  let lastError: unknown;
  while (Date.now() < deadline) {
    try {
      return await httpGetOnce(url);
    } catch (err) {
      lastError = err;
      await new Promise((r) => setTimeout(r, 150));
    }
  }
  throw lastError;
}

function isProcessAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

describe("ChatProxyManager (real subprocess, not mocked)", () => {
  jest.setTimeout(30000);

  function makeManager(port: number, onStateChange?: (s: ProxyState, d?: string) => void) {
    return new ChatProxyManager(
      PYTHON,
      ["-m", "decoy.cli", "chat-proxy", "--port", String(port)],
      { onStateChange },
    );
  }

  test("start() spawns a real process that actually serves /healthz, replacing the manual `decoy chat-proxy` terminal step", async () => {
    const manager = makeManager(TEST_PORT);
    manager.start({
      ...process.env,
      PYTHONPATH: path.join(REPO_ROOT, "src"),
      ANTHROPIC_API_KEY: "sk-ant-test-not-real",
    } as NodeJS.ProcessEnv);

    await waitForState(manager, "running");
    const pid = manager.getPid();
    expect(pid).toBeGreaterThan(0);
    expect(isProcessAlive(pid!)).toBe(true);

    const resp = await httpGet(`http://127.0.0.1:${TEST_PORT}/healthz`);
    expect(resp.status).toBe(200);
    expect(JSON.parse(resp.body)).toEqual({ status: "ok", service: "decoy-chat-proxy" });

    await manager.stopAndWait();
    expect(manager.getState()).toBe("stopped");
    // The core claim of this phase: no orphaned background process left
    // running once the manager says "stopped".
    expect(isProcessAlive(pid!)).toBe(false);
  });

  test("stop() then dispose() leaves no orphaned process (the IDE-close requirement)", async () => {
    const manager = makeManager(TEST_PORT + 1);
    manager.start({
      ...process.env,
      PYTHONPATH: path.join(REPO_ROOT, "src"),
      ANTHROPIC_API_KEY: "sk-ant-test-not-real",
    } as NodeJS.ProcessEnv);
    await waitForState(manager, "running");
    const pid = manager.getPid()!;

    await manager.dispose();

    expect(manager.getState()).toBe("stopped");
    expect(isProcessAlive(pid)).toBe(false);
  });

  test("restart() actually cycles the process -- old pid gone, new pid serving", async () => {
    const manager = makeManager(TEST_PORT + 2);
    const env = {
      ...process.env,
      PYTHONPATH: path.join(REPO_ROOT, "src"),
      ANTHROPIC_API_KEY: "sk-ant-test-not-real",
    } as NodeJS.ProcessEnv;

    manager.start(env);
    await waitForState(manager, "running");
    const firstPid = manager.getPid()!;

    await manager.restart(env);
    await waitForState(manager, "running");
    const secondPid = manager.getPid()!;

    expect(secondPid).not.toBe(firstPid);
    expect(isProcessAlive(firstPid)).toBe(false);
    expect(isProcessAlive(secondPid)).toBe(true);

    const resp = await httpGet(`http://127.0.0.1:${TEST_PORT + 2}/healthz`);
    expect(resp.status).toBe(200);

    await manager.dispose();
  });

  test("calling start() twice while already running does not spawn a second process", async () => {
    const manager = makeManager(TEST_PORT + 3);
    const env = {
      ...process.env,
      PYTHONPATH: path.join(REPO_ROOT, "src"),
      ANTHROPIC_API_KEY: "sk-ant-test-not-real",
    } as NodeJS.ProcessEnv;

    manager.start(env);
    await waitForState(manager, "running");
    const pid = manager.getPid();

    manager.start(env); // should be a no-op
    expect(manager.getPid()).toBe(pid);

    await manager.dispose();
  });

  test("a bogus command reports state=error instead of hanging or throwing uncaught", async () => {
    const manager = new ChatProxyManager("this-command-does-not-exist-xyz", ["--port", "1"]);
    manager.start(process.env as NodeJS.ProcessEnv);
    await waitForState(manager, "error");
    expect(manager.getLastError()).toBeTruthy();
  });
});

import { ChildProcess, spawn } from "child_process";

/**
 * Manages the chat proxy's ENTIRE process lifecycle from inside the
 * extension -- the manual step this replaces: before this, getting a
 * masked request to show up in the panel required the user to open a
 * terminal, `export ANTHROPIC_API_KEY=...`, run `decoy chat-proxy`
 * themselves, and keep that terminal open for the life of the editing
 * session. None of that should be visible to an installed-plugin user.
 *
 * Deliberately decoupled from "the bundled binary" specifically --
 * `command`/`args` are passed in by the caller (extension.ts passes the
 * Phase 12 bundled `decoy-proxy` binary path in production; tests pass a
 * real `python -m decoy.cli` invocation) so this class's actual process
 * lifecycle handling (spawn, track state, kill cleanly, never leave an
 * orphan) is testable against a REAL child process, not a mock -- the
 * whole point of this phase is proving manual steps are gone, not that
 * code exists that should remove them.
 */

export type ProxyState = "stopped" | "starting" | "running" | "stopping" | "error";

export type SpawnFn = (command: string, args: string[], options: { env: NodeJS.ProcessEnv }) => ChildProcess;

export interface ChatProxyManagerEvents {
  onStateChange?(state: ProxyState, detail?: string): void;
}

export class ChatProxyManager {
  private child: ChildProcess | undefined;
  private state: ProxyState = "stopped";
  private lastError: string | undefined;
  private intentionalStop = false;

  constructor(
    private readonly command: string,
    private readonly args: string[],
    private readonly events: ChatProxyManagerEvents = {},
    private readonly spawnFn: SpawnFn = spawn,
  ) {}

  getState(): ProxyState {
    return this.state;
  }

  getLastError(): string | undefined {
    return this.lastError;
  }

  getPid(): number | undefined {
    return this.child?.pid;
  }

  private setState(state: ProxyState, detail?: string): void {
    this.state = state;
    if (detail) this.lastError = detail;
    this.events.onStateChange?.(state, detail);
  }

  /** No-op if already running or starting -- calling start() twice in a
   * row (e.g. a double-click on "Start") must never spawn two processes. */
  start(env: NodeJS.ProcessEnv): void {
    if (this.state === "running" || this.state === "starting") {
      return;
    }
    this.intentionalStop = false;
    this.setState("starting");

    let child: ChildProcess;
    try {
      child = this.spawnFn(this.command, this.args, { env });
    } catch (err) {
      this.setState("error", err instanceof Error ? err.message : String(err));
      return;
    }
    this.child = child;

    child.once("spawn", () => {
      // A process can spawn successfully and still exit immediately
      // (e.g. missing extra, bad port) -- only report "running" if it's
      // still our current child by the time this fires.
      if (this.child === child) {
        this.setState("running");
      }
    });

    child.once("error", (err) => {
      if (this.child === child) {
        this.child = undefined;
        this.setState("error", err.message);
      }
    });

    child.once("exit", (code, signal) => {
      if (this.child !== child) {
        return; // already superseded by a restart
      }
      this.child = undefined;
      if (this.intentionalStop) {
        this.setState("stopped");
      } else if (code === 0) {
        this.setState("stopped");
      } else {
        this.setState("error", `proxy exited unexpectedly (code=${code}, signal=${signal})`);
      }
    });
  }

  /** Sends SIGTERM and returns immediately -- callers that need to know
   * when the process has actually exited should listen via
   * ChatProxyManagerEvents.onStateChange for a transition to "stopped",
   * or use stopAndWait() below. */
  stop(): void {
    if (!this.child) {
      this.setState("stopped");
      return;
    }
    this.intentionalStop = true;
    this.setState("stopping");
    this.child.kill();
  }

  /** Awaits actual process exit -- used by dispose() (IDE shutdown must
   * not return while a child process is still tearing down) and by
   * restart() (must not start a new process before the old one's port is
   * free). */
  async stopAndWait(timeoutMs = 5000): Promise<void> {
    if (!this.child) {
      this.setState("stopped");
      return;
    }
    const child = this.child;
    this.intentionalStop = true;
    this.setState("stopping");
    await new Promise<void>((resolve) => {
      const timer = setTimeout(() => {
        // Escalate: SIGTERM was ignored within the timeout -- SIGKILL
        // rather than leave an orphaned process behind, which is exactly
        // the failure mode this class exists to prevent.
        child.kill("SIGKILL");
      }, timeoutMs);
      child.once("exit", () => {
        clearTimeout(timer);
        resolve();
      });
      child.kill();
    });
  }

  async restart(env: NodeJS.ProcessEnv): Promise<void> {
    await this.stopAndWait();
    this.start(env);
  }

  /** Call from the extension's deactivate()/dispose path. Fire-and-forget
   * is NOT safe here -- VS Code can tear down the extension host process
   * itself shortly after deactivate() returns, so this must be awaited by
   * the caller (see extension.ts) or a killed-but-not-yet-reaped child
   * can survive the editor closing. */
  async dispose(): Promise<void> {
    await this.stopAndWait();
  }
}

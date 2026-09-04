"""The `decoy` command-line entry point.

Deliberately minimal -- a headless way to inspect and clear local state
(audit log, overrides) without either IDE extension open, useful for CI,
scripts, or a terminal-only environment. It does not run any masking
itself; that's a library operation (see unified_pipeline.ask_over_data
and record_masker.mask_records) with no standalone CLI wrapper, since
"mask this data" always needs a real fetch_records_fn/session context
that only calling code can supply.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .audit_log import get_default_audit_log
from .overrides import get_default_store


def _cmd_version(_args: argparse.Namespace) -> int:
    print(__version__)
    return 0


def _cmd_audit_list(args: argparse.Namespace) -> int:
    audit_log = get_default_audit_log()
    entries = audit_log.get_entries(session_id=args.session)
    if args.json:
        print(json.dumps(entries, indent=2))
    else:
        if not entries:
            print("No audit entries recorded.")
        for e in entries:
            print(
                f"{e['timestamp']}  [{e['source']}]  {e['field']:20s} "
                f"{e['decision']:10s} ({e['layer']})  {e['reason']}"
            )
    return 0


def _cmd_audit_clear_session(args: argparse.Namespace) -> int:
    """Clear session data only (audit + vault mappings), keeping
    configured overrides -- mirrors both IDE extensions' "Clear Session
    Data Only" action, same underlying files."""
    from .vault import get_default_manager

    audit_log = get_default_audit_log()
    removed = audit_log.clear(session_id=args.session)
    get_default_manager().clear_session(args.session) if args.session else get_default_manager().clear_all()
    scope = f"session {args.session!r}" if args.session else "all sessions"
    print(f"Cleared {removed} audit entr{'y' if removed == 1 else 'ies'} and vault mappings for {scope}.")
    print("Overrides were NOT touched -- use `decoy audit clear-all` to remove those too.")
    return 0


def _cmd_audit_clear_all(_args: argparse.Namespace) -> int:
    """Clear everything: audit log, vault, AND configured overrides --
    mirrors both IDE extensions' "Clear All Local Data" action. No
    unstated exception, per WHAT_THIS_PROTECTS_AGAINST.md's clearing
    section."""
    from .vault import get_default_manager

    audit_log = get_default_audit_log()
    removed = audit_log.clear()
    get_default_manager().clear_all()
    get_default_store().reload(force=True)
    override_path = get_default_store().path
    if override_path.exists():
        override_path.write_text(json.dumps({"always_mask": {}, "never_mask": {}}, indent=2))
    print(f"Cleared {removed} audit entr{'y' if removed == 1 else 'ies'}, all vault mappings, and all overrides.")
    return 0


def _cmd_proxy(args: argparse.Namespace) -> int:
    """Run the MCP masking proxy (decoy.mcp_proxy). Imported lazily so
    `decoy version`/`audit`/`overrides` don't require the `mcp` extra to
    be installed -- only `decoy proxy` does.
    """
    from .mcp_proxy import main as proxy_main

    proxy_argv = ["--session", args.session, args.target_command, *args.target_args]
    return proxy_main(proxy_argv)


def _cmd_chat_proxy(args: argparse.Namespace) -> int:
    """Run the chat-completions masking proxy (decoy.chat_proxy). Imported
    lazily so `decoy version`/`audit`/`overrides`/`proxy` don't require
    the `chat-proxy` extra to be installed -- only `decoy chat-proxy` does.
    """
    from .chat_proxy import main as chat_proxy_main

    return chat_proxy_main(["--host", args.host, "--port", str(args.port)])


def _cmd_overrides_show(_args: argparse.Namespace) -> int:
    store = get_default_store()
    rules = store.rules()
    print(f"Overrides file: {store.path}")
    print("always_mask:")
    print(f"  field_names: {sorted(rules.always_mask_field_names)}")
    print(f"  patterns:    {[p.pattern for p in rules.always_mask_patterns]}")
    print("never_mask:")
    print(f"  field_names: {sorted(rules.never_mask_field_names)}")
    print(f"  patterns:    {[p.pattern for p in rules.never_mask_patterns]}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="decoy", description="Decoy: local masking-layer utilities.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("version", help="print the installed decoy version").set_defaults(func=_cmd_version)

    audit = subparsers.add_parser("audit", help="inspect or clear the local audit trail")
    audit_sub = audit.add_subparsers(dest="audit_command", required=True)

    audit_list = audit_sub.add_parser("list", help="list audit entries")
    audit_list.add_argument("--session", default=None, help="filter to one session_id")
    audit_list.add_argument("--json", action="store_true", help="output raw JSON instead of a summary line per entry")
    audit_list.set_defaults(func=_cmd_audit_list)

    audit_clear = audit_sub.add_parser(
        "clear", help="clear audit + vault for one session (or all sessions) -- keeps overrides"
    )
    audit_clear.add_argument("--session", default=None, help="clear only this session_id; omit to clear all sessions")
    audit_clear.set_defaults(func=_cmd_audit_clear_session)

    audit_clear_all = audit_sub.add_parser(
        "clear-all", help="clear EVERYTHING: audit, vault, and overrides -- no exceptions"
    )
    audit_clear_all.set_defaults(func=_cmd_audit_clear_all)

    proxy = subparsers.add_parser(
        "proxy",
        help="run the MCP masking proxy in front of a real target MCP server (requires the `mcp` extra)",
        description=(
            "Connects to a real target MCP server as a subprocess and re-exposes its tools "
            "over this process's own stdio, masking every tool result before it reaches the "
            "calling client (e.g. Claude Code). Point Claude Code (or any MCP client) at "
            "`decoy proxy -- <target-command> [target-args...]` instead of the target server "
            "directly."
        ),
    )
    proxy.add_argument("--session", default="mcp-proxy", help="session_id to mask/vault under")
    proxy.add_argument(
        "target_command", help="the real target MCP server's executable, e.g. `npx` or `/path/to/server`"
    )
    proxy.add_argument(
        "target_args", nargs=argparse.REMAINDER, help="arguments passed through to the target MCP server"
    )
    proxy.set_defaults(func=_cmd_proxy)

    chat_proxy = subparsers.add_parser(
        "chat-proxy",
        help="run the chat-completions masking proxy in front of a real LLM API (requires the `chat-proxy` extra)",
        description=(
            "Runs a local HTTP proxy exposing Anthropic's /v1/messages and OpenAI's "
            "/v1/chat/completions shapes: masks outgoing message text, forwards to the real "
            "provider using a server-side API key (ANTHROPIC_API_KEY/OPENAI_API_KEY -- never "
            "read from the client request), and unmasks the response. Point ANTHROPIC_BASE_URL "
            "or OPENAI_BASE_URL at this proxy instead of the real provider. See "
            "decoy.chat_proxy's module docstring for session-id coordination with `decoy proxy` "
            "and the DECOY_VAULT_PERSIST requirement to share one vault across both."
        ),
    )
    chat_proxy.add_argument("--host", default="127.0.0.1")
    chat_proxy.add_argument("--port", type=int, default=8787)
    chat_proxy.set_defaults(func=_cmd_chat_proxy)

    overrides = subparsers.add_parser("overrides", help="inspect the local overrides file")
    overrides_sub = overrides.add_subparsers(dest="overrides_command", required=True)
    overrides_show = overrides_sub.add_parser("show", help="print the current always_mask/never_mask rules")
    overrides_show.set_defaults(func=_cmd_overrides_show)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:  # noqa: BLE001 - a CLI failure should print cleanly, not stack-trace by default
        print(f"decoy: error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

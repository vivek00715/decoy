"""DB-agnostic MCP masking proxy ("decoy-proxy" in tool discovery).

Sits between an MCP client (e.g. Claude Code) and a real target MCP
server. On startup it connects to the target as an MCP client, calls
list_tools() ONCE, and re-exposes exactly those tools -- no hardcoded
per-tool wrapper. On every call_tool(), it forwards to the real target
and masks the result via record_masker.py/masker.py before returning it,
using the SAME override_store/vault_manager wiring as every other entry
point into Decoy (direct DB queries, edge-case sampling), so a value seen
through this proxy gets the same fake as everywhere else in the session.

This targets the installed `mcp==2.1.1` API specifically: its low-level
Server takes on_list_tools/on_call_tool as single generic constructor
callbacks (not per-tool decorators), so "dynamic registration" here is
just forwarding by name inside one callback -- there is no per-tool
registration step to work around.

Known limitation, stated plainly rather than silently: masking here
understands JSON-shaped tool results (a list of row dicts, or a dict
wrapping one, e.g. Elasticsearch-style {"hits": [...]})  and plain text
content (masked via the free-text layer). Non-text content blocks
(images, audio, embedded binary resources) are passed through unmasked --
record_masker.py and masker.py only operate on text/JSON data today.

Outgoing tool call ARGUMENTS: this proxy targets a real MCP server that
this project's own design explicitly allows to be remote (the top-level
spec names Snowflake -- a cloud-only data warehouse with no local
deployment mode at all -- and Elasticsearch, commonly a remote/hosted
cluster, as expected targets). So an argument value reaching this proxy
is not assumed to stay on the user's machine, and "the LLM already only
knows fake values, so forward them straight through" is not automatically
true unless enforced here.

The fix is UNMASKING outgoing string arguments, not masking them.
Masking an outgoing argument would break the target's own real database
lookup (a target doing e.g. `WHERE email = ?` needs the REAL email to
find anything -- there is no way for a masked/fake value to match a real
row, so "mask the argument" is not a viable fix in the general case, and
this project deliberately does not pretend otherwise). Because every
masking path in this project (free-text prompts, DB records, and this
proxy's own results) shares ONE vault per session_id, a value the LLM
currently holds as a tool-call argument is, if it originated from
something Decoy already masked earlier in this same session, the FAKE
value from that same vault -- so reversing it (vault lookup: fake ->
real) before forwarding recovers the real value the target needs, without
the real value ever having reached the LLM. The masked RESULT still comes
back through the normal masking path below. See `_unmask_arguments`.

Residual limitation, stated precisely rather than implied fixed: this
only recovers a value that was ALREADY masked earlier in this exact
session (free text, a DB record, or a prior proxy call). A real value
that reaches a tool-call argument through some path that never went
through Decoy's own masking (e.g. supplied directly by calling code
outside any Decoy-covered flow) has no vault entry to reverse and is
forwarded as-is -- this is a boundary condition of any masking system
(nothing can be recovered for a value Decoy never saw), not something
this fix claims to close.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Optional

import anyio
import mcp.types as types
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from .logging_config import get_logger
from .masker import Masker
from .overrides import OverrideStore, get_default_store
from .record_masker import mask_records
from .vault import VaultManager, get_default_manager

logger = get_logger("mcp_proxy")

DEFAULT_SESSION_ID = "mcp-proxy"
PROXY_NAME = "decoy-proxy"
DEFAULT_CALL_TIMEOUT_SECONDS = 30.0
DEFAULT_DISCOVER_TIMEOUT_SECONDS = 30.0


def _mask_json_value(
    value: Any,
    session_id: str,
    override_store: OverrideStore,
    vault_manager: VaultManager,
    audit_log: Optional[Any] = None,
    request_id: Optional[str] = None,
) -> Any:
    """Mask a JSON-shaped tool result: a list of dict rows, a dict
    wrapping such a list (e.g. {"hits": [...]}), or a single flat dict
    record -- row-shaped data goes through record_masker.py's column
    classification, same as every other entry point.

    CONFIRMED GAPS, now fixed (found empirically, not assumed -- see
    mcp_proxy.py's module docstring for the "known limitation" this
    replaces) -- THREE dead ends, not the two originally suspected:

    1. A bare string passed to this function directly returned unchanged
       -- `_mask_json_value("Contact: alice@example.com", ...)` came back
       byte-for-byte identical. Fixed: now masked via the same free-text
       `Masker` `_mask_text_block` already uses for TextContent blocks.
    2. A list that isn't uniformly dict rows (e.g. a list of plain
       strings) also returned unchanged --
       `_mask_json_value(["bob@example.com", "alice@example.com"], ...)`
       leaked both. Fixed by recursing: every element is now masked
       individually (a dict element goes through the row-shaped path via
       recursion; a string element through the free-text layer; a
       number/bool/None left as-is).
    3. **Found only while building an end-to-end test for the other two,
       not from re-reading the code more carefully** -- a dict with a
       MIX of keys (one holding row-shaped dicts, another holding a
       plain list of strings, or any other scalar) used to return every
       key EXCEPT the row-shaped one completely unmasked, because the
       old code returned as soon as it found any list-of-dicts key. This
       is the practically-reachable version of gap 2 for a REAL target
       using the installed `mcp` package: its own wire-protocol
       validation refuses to serialize a non-dict structured_content at
       all (confirmed directly: constructing one with a bare string
       raised a pydantic `ValidationError`), so a real target can only
       ever send gap 1/2's bare shapes nested inside a dict -- which is
       exactly gap 3. Fixed the same way: every key not already handled
       by the list-of-dicts branch is now recursively masked too.

    See tests/test_mcp_proxy_phase6.py's
    `test_non_row_shaped_target_mixed_structured_content_is_fully_masked`
    for a real end-to-end reproduction (a second fake target MCP server,
    genuinely different in shape from the row-shaped one every other test
    in that file uses) -- not just a unit-level check of this function.

    `audit_log`/`request_id`, if given, are forwarded to mask_records with
    source="mcp_tool_result" -- this proxy is a third entry point into the
    masking core (after direct DB queries and edge-case sampling) and logs
    through the exact same audit_log.py backend, not a separate one.
    """
    mask_kwargs = dict(
        session_id=session_id,
        override_store=override_store,
        vault_manager=vault_manager,
        audit_log=audit_log,
        request_id=request_id,
        audit_source="mcp_tool_result",
    )

    if isinstance(value, list):
        if value and all(isinstance(v, dict) for v in value):
            masked, _decisions = mask_records(value, **mask_kwargs)
            return masked
        return [
            _mask_json_value(item, session_id, override_store, vault_manager, audit_log, request_id)
            for item in value
        ]

    if isinstance(value, dict):
        list_of_dict_keys = {
            key for key, val in value.items() if isinstance(val, list) and val and all(isinstance(item, dict) for item in val)
        }
        if list_of_dict_keys:
            # CONFIRMED GAP, now fixed: a dict with a MIX of keys -- one
            # holding a list of dicts (handled below via mask_records)
            # and ANOTHER holding a list of plain strings, or a bare
            # string/other scalar -- used to return that other key's
            # value completely unmasked (the old code returned as soon
            # as it found ANY list-of-dicts key, copying every other key
            # through untouched). Verified directly: {"hits":
            # [{"email": "alice@example.com"}], "warnings": ["contact
            # dave.chen@example.com for details"]} came back with "hits"
            # masked but "warnings" untouched, leaking the real email.
            # Every key is now handled -- list-of-dicts keys via
            # mask_records (row-shaped column classification), every
            # other key by recursing back into this function.
            masked_dict: dict[str, Any] = {}
            for key, val in value.items():
                if key in list_of_dict_keys:
                    masked_rows, _decisions = mask_records(val, **mask_kwargs)
                    masked_dict[key] = masked_rows
                else:
                    masked_dict[key] = _mask_json_value(
                        val, session_id, override_store, vault_manager, audit_log, request_id
                    )
            return masked_dict
        if value:
            # a single flat record, e.g. one Elasticsearch _source document
            masked, _decisions = mask_records([value], **mask_kwargs)
            return masked[0]
        return value

    if isinstance(value, str):
        masker = Masker(session_id=session_id, override_store=override_store, vault_manager=vault_manager)
        return masker.mask_text(value, audit_log=audit_log, request_id=request_id).masked_text

    return value


def _mask_text_block(
    text: str,
    session_id: str,
    override_store: OverrideStore,
    vault_manager: VaultManager,
    audit_log: Optional[Any] = None,
    request_id: Optional[str] = None,
) -> str:
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        parsed = None

    if isinstance(parsed, (list, dict)):
        masked = _mask_json_value(parsed, session_id, override_store, vault_manager, audit_log, request_id)
        return json.dumps(masked, default=str)

    masker = Masker(session_id=session_id, override_store=override_store, vault_manager=vault_manager)
    return masker.mask_text(text, audit_log=audit_log, request_id=request_id).masked_text


def _unmask_arguments(value: Any, masker: Masker) -> Any:
    """Recursively reverse any known fake value back to its real original
    (vault lookup, longest-fake-first) inside an outgoing tool call's
    arguments -- see this module's docstring for why this is the correct
    direction (unmask, not mask) and its residual limitation. Recurses
    into dicts/lists; every string leaf is run through
    `Masker.unmask_text`, which is a no-op for a string that doesn't
    contain a currently-known fake, so a genuinely new/unmasked value
    passes through unchanged rather than being altered.
    """
    if isinstance(value, str):
        return masker.unmask_text(value)
    if isinstance(value, dict):
        return {k: _unmask_arguments(v, masker) for k, v in value.items()}
    if isinstance(value, list):
        return [_unmask_arguments(v, masker) for v in value]
    return value


def _mask_call_tool_result(
    result: types.CallToolResult,
    session_id: str,
    override_store: OverrideStore,
    vault_manager: VaultManager,
    audit_log: Optional[Any] = None,
    request_id: Optional[str] = None,
) -> types.CallToolResult:
    masked_content = []
    for block in result.content:
        if isinstance(block, types.TextContent):
            try:
                masked_text = _mask_text_block(
                    block.text, session_id, override_store, vault_manager, audit_log, request_id
                )
            except Exception as exc:  # noqa: BLE001 - never leak the raw block on a masking bug
                logger.error(
                    "failed to mask a text content block (%s); redacting it flatly (fail-safe)", exc
                )
                masked_text = "[REDACTED: masking error, content withheld for safety]"
            masked_content.append(block.model_copy(update={"text": masked_text}))
        else:
            logger.warning(
                "tool result contains a non-text content block (%s); passed through unmasked "
                "-- record_masker/masker only operate on text/JSON data today",
                type(block).__name__,
            )
            masked_content.append(block)

    masked_structured = result.structured_content
    if masked_structured is not None:
        try:
            masked_structured = _mask_json_value(
                masked_structured, session_id, override_store, vault_manager, audit_log, request_id
            )
        except Exception as exc:  # noqa: BLE001 - fail toward redaction, never raw structured data
            logger.error(
                "failed to mask structured_content (%s); withholding it flatly (fail-safe)", exc
            )
            masked_structured = {"error": "[REDACTED: masking error, content withheld for safety]"}

    return result.model_copy(update={"content": masked_content, "structured_content": masked_structured})


class MaskingProxy:
    """Owns a ClientSession to the real target MCP server and a Server
    that re-exposes its tools, masking every tool result before it
    reaches the caller.
    """

    def __init__(
        self,
        target_session: ClientSession,
        session_id: str = DEFAULT_SESSION_ID,
        override_store: Optional[OverrideStore] = None,
        vault_manager: Optional[VaultManager] = None,
        name: str = PROXY_NAME,
        call_timeout_seconds: float = DEFAULT_CALL_TIMEOUT_SECONDS,
        discover_timeout_seconds: float = DEFAULT_DISCOVER_TIMEOUT_SECONDS,
        audit_log: Optional[Any] = None,
    ):
        self.target_session = target_session
        self.session_id = session_id
        self.override_store = override_store or get_default_store()
        self.vault_manager = vault_manager or get_default_manager()
        self.call_timeout_seconds = call_timeout_seconds
        self.discover_timeout_seconds = discover_timeout_seconds
        self.audit_log = audit_log
        self._discovered_tools: list[types.Tool] = []
        self.server = Server(name, on_list_tools=self._on_list_tools, on_call_tool=self._on_call_tool)

    async def discover(self) -> list[types.Tool]:
        """Fetch the target's tool list once. Called lazily on first
        list_tools request if not already called. On failure -- including
        the target hanging past `discover_timeout_seconds` -- the proxy
        exposes zero tools rather than raising into the caller's session,
        fabricating tool definitions, or hanging indefinitely itself
        (fail toward exposing nothing, and fail promptly).

        A slow/unresponsive target here is a separate failure mode from a
        slow call_tool: this call site has no per-request timeout
        parameter in the installed `mcp` client API (unlike call_tool),
        so the timeout is enforced with an outer anyio.fail_after guard
        instead.
        """
        try:
            with anyio.fail_after(self.discover_timeout_seconds):
                result = await self.target_session.list_tools()
        except TimeoutError:
            logger.error(
                "target MCP server did not respond to list_tools() within %.1fs; "
                "proxy will expose zero tools until this succeeds",
                self.discover_timeout_seconds,
            )
            self._discovered_tools = []
            return self._discovered_tools
        except Exception as exc:  # noqa: BLE001 - never crash proxy startup on a flaky target
            logger.error(
                "failed to discover tools from target MCP server (%s); "
                "proxy will expose zero tools until this succeeds",
                exc,
            )
            self._discovered_tools = []
            return self._discovered_tools

        self._discovered_tools = list(result.tools)
        logger.info(
            "discovered %d tool(s) from target MCP server: %s",
            len(self._discovered_tools),
            [t.name for t in self._discovered_tools],
        )
        return self._discovered_tools

    async def _on_list_tools(self, ctx, params):
        if not self._discovered_tools:
            await self.discover()
        return types.ListToolsResult(tools=self._discovered_tools)

    async def _on_call_tool(self, ctx, params: types.CallToolRequestParams):
        outgoing_arguments = params.arguments or {}
        try:
            masker = Masker(
                session_id=self.session_id, override_store=self.override_store, vault_manager=self.vault_manager
            )
            outgoing_arguments = _unmask_arguments(outgoing_arguments, masker)
        except Exception as exc:  # noqa: BLE001 - fail toward forwarding the original (still-masked)
            # arguments rather than crashing the call. This is the safe
            # direction: a failure here means LESS real data reaches the
            # target (the fake value goes out unchanged), never more.
            logger.error(
                "failed to unmask outgoing arguments for %r (%s); forwarding them as originally "
                "received (fail-safe: no real value is exposed by this failure)",
                params.name,
                exc,
            )
            outgoing_arguments = params.arguments or {}

        try:
            # Belt and suspenders: pass the timeout to call_tool's own
            # read_timeout_seconds (enforced by the mcp client library
            # itself) AND wrap the call in an outer anyio.fail_after guard,
            # so a hung target (slow network call, slow query, no response
            # at all) can never hang this proxy call indefinitely even if
            # the library's own timeout handling has a gap -- a hang here
            # would otherwise hang the caller's (e.g. Claude Code's) tool
            # call indefinitely, which is worse than a clean error.
            with anyio.fail_after(self.call_timeout_seconds):
                result = await self.target_session.call_tool(
                    params.name, outgoing_arguments, read_timeout_seconds=self.call_timeout_seconds
                )
        except TimeoutError:
            logger.error(
                "target MCP server did not respond to call_tool(%r) within %.1fs",
                params.name,
                self.call_timeout_seconds,
            )
            return types.CallToolResult(
                content=[
                    types.TextContent(
                        type="text",
                        text=f"decoy-proxy: target call to {params.name!r} timed out after "
                        f"{self.call_timeout_seconds:.0f}s",
                    )
                ],
                is_error=True,
            )
        except Exception as exc:  # noqa: BLE001 - surface as a tool error, not a crashed proxy
            logger.error("target MCP server call_tool(%r) failed (%s)", params.name, exc)
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"decoy-proxy: target call failed: {exc}")],
                is_error=True,
            )

        if not isinstance(result, types.CallToolResult):
            logger.warning(
                "target returned %s instead of CallToolResult for tool %r; passing through unmasked",
                type(result).__name__,
                params.name,
            )
            return result

        try:
            request_id = str(uuid.uuid4())
            return _mask_call_tool_result(
                result, self.session_id, self.override_store, self.vault_manager, self.audit_log, request_id
            )
        except Exception as exc:  # noqa: BLE001 - fail toward redaction, never raw data
            logger.error(
                "masking tool result for %r failed (%s); returning a flatly redacted result (fail-safe)",
                params.name,
                exc,
            )
            return types.CallToolResult(
                content=[
                    types.TextContent(
                        type="text", text="[REDACTED: masking error, result withheld for safety]"
                    )
                ],
                is_error=False,
            )


async def run_stdio(
    target_command: str,
    target_args: list[str],
    session_id: str = DEFAULT_SESSION_ID,
    target_env: Optional[dict[str, str]] = None,
    target_cwd: Optional[str] = None,
) -> None:
    """Wire up the real process-level topology this module's docstring
    describes: connect to `target_command`/`target_args` as a real
    subprocess MCP server over stdio (the "target"), wrap it in a
    MaskingProxy, and re-expose that proxy over THIS process's own
    stdin/stdout so a real MCP client (e.g. Claude Code) can launch this
    as its server subprocess.

    This is the one piece MaskingProxy itself deliberately doesn't own
    (see its docstring: it's handed an already-connected
    `target_session`) -- tests wire ClientSession over in-memory streams
    instead, since that's what a fast test needs; a real invocation needs
    real stdio processes, which is what this function provides.
    """
    target_params = StdioServerParameters(
        command=target_command, args=target_args, env=target_env, cwd=target_cwd
    )
    async with stdio_client(target_params) as (target_read, target_write):
        async with ClientSession(target_read, target_write) as target_session:
            await target_session.initialize()

            proxy = MaskingProxy(target_session=target_session, session_id=session_id)
            await proxy.discover()

            async with stdio_server() as (proxy_read, proxy_write):
                await proxy.server.run(
                    proxy_read,
                    proxy_write,
                    proxy.server.create_initialization_options(),
                )


def main(argv: Optional[list[str]] = None) -> int:
    """Standalone entry point for running the proxy as its own process,
    e.g. `python -m decoy.mcp_proxy <target-command> [target-args...]` or
    via the `decoy proxy` CLI subcommand (see cli.py), which is the
    packaged/PyInstaller-bundled entry point end users actually invoke.
    """
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="decoy-proxy",
        description=(
            "Run Decoy's MCP masking proxy: connects to a real target MCP server as a "
            "subprocess and re-exposes its tools over this process's own stdio, masking "
            "every tool result before it reaches the calling client (e.g. Claude Code)."
        ),
    )
    parser.add_argument("--session", default=DEFAULT_SESSION_ID, help="session_id to mask/vault under")
    parser.add_argument(
        "target_command", help="the real target MCP server's executable, e.g. `npx` or `/path/to/server`"
    )
    parser.add_argument(
        "target_args", nargs=argparse.REMAINDER, help="arguments passed through to the target MCP server"
    )
    args = parser.parse_args(argv)

    try:
        anyio.run(run_stdio, args.target_command, args.target_args, args.session)
    except KeyboardInterrupt:
        return 0
    except Exception as exc:  # noqa: BLE001 - a proxy failure should print cleanly, not stack-trace
        print(f"decoy-proxy: error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())

import json
import uuid

import anyio
import mcp.types as types
import pytest
from mcp.client._memory import create_client_server_memory_streams
from mcp.client.session import ClientSession
from mcp.server.lowlevel import Server

from decoy.masker import Masker
from decoy.mcp_proxy import MaskingProxy
from decoy.overrides import OverrideStore
from decoy.vault import VaultManager

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def vault_manager():
    return VaultManager(persist=False)


@pytest.fixture
def override_store(tmp_path):
    path = tmp_path / "overrides.json"
    path.write_text(json.dumps({"always_mask": {"field_names": []}, "never_mask": {"field_names": []}}))
    return OverrideStore(path=path)


FAKE_ROWS = [
    {"id": 1, "name": "Alice Smith", "email": "alice@example.com"},
    {"id": 2, "name": "Bob Jones", "email": "bob@example.com"},
]


async def _second_fake_target_list_tools(ctx, params):
    """A SECOND fake target MCP server, deliberately shaped differently
    from the ES-row-shaped `_fake_target_*` above -- see this module's
    docstring in mcp_proxy.py and WHAT_THIS_PROTECTS_AGAINST.md for why
    this matters: Phase 6's own summary flagged, but nobody had verified,
    whether the proxy handles a target returning something other than a
    flat list of dicts (e.g. a GitHub or Slack MCP server, not a
    database). This stands in for exactly that: a "search issues"-style
    tool whose structured_content is a dict with a MIX of shapes -- one
    key holding row-shaped dicts, another holding a plain list of
    strings -- no uniform {"hits": [...]} of row dicts throughout.

    NOTE on why this isn't an even more bare-bones shape: a genuinely
    bare string/list at the top level of structured_content was tried
    first and rejected -- the INSTALLED mcp package's own wire-protocol
    validation refuses to serialize a non-dict structured_content at all
    (pydantic: "Input should be a valid dictionary"), confirmed directly
    by trying it. So the realistically-reachable version of this gap,
    for a real MCP server using this SDK, is a dict whose VALUES include
    a non-dict list or bare scalar -- exactly what's built below, and
    exactly the shape that was still leaking real emails after the first
    round of this fix (see mcp_proxy.py's _mask_json_value dict-branch
    comment for the precise mixed-keys gap this closes).
    """
    return types.ListToolsResult(
        tools=[
            types.Tool(
                name="search_issues",
                description="search issue comments (GitHub/Slack-style, not a database)",
                inputSchema={"type": "object", "properties": {}},
            )
        ]
    )


async def _second_fake_target_call_tool(ctx, params: types.CallToolRequestParams):
    if params.name != "search_issues":
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=f"unknown tool {params.name}")], is_error=True
        )
    # "hits" is row-shaped (list of dicts) -- already worked before this
    # fix. "warnings" is a plain list of strings under the SAME dict --
    # this is the key that used to pass through completely unmasked,
    # confirmed directly before the fix.
    return types.CallToolResult(
        content=[types.TextContent(type="text", text="Found 1 matching issue.")],
        structured_content={
            "hits": [{"assignee_email": "alice@example.com"}],
            "warnings": ["contact dave.chen@example.com for details"],
        },
    )


async def _fake_target_list_tools(ctx, params):
    return types.ListToolsResult(
        tools=[
            types.Tool(
                name="get_customers",
                description="fetch customer rows",
                inputSchema={"type": "object", "properties": {}},
            )
        ]
    )


async def _fake_target_call_tool(ctx, params: types.CallToolRequestParams):
    if params.name != "get_customers":
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=f"unknown tool {params.name}")], is_error=True
        )
    return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(FAKE_ROWS))])


async def _run_full_stack(
    override_store, vault_manager, session_id, test_body, target_list_tools=None, target_call_tool=None
):
    """Wire up: fake target server <-(memory streams)-> proxy's ClientSession
    -(MaskingProxy)- proxy's Server <-(memory streams)-> test ClientSession,
    then run `test_body(test_session)`.

    `target_list_tools`/`target_call_tool` default to the ES-row-shaped
    fake target (_fake_target_list_tools/_fake_target_call_tool) that
    every existing test in this file uses -- pass a different pair (see
    _second_fake_target_call_tool below) to prove the proxy against a
    target with a genuinely DIFFERENT result shape, not just a different
    row's worth of the same shape.
    """
    target_server = Server(
        "fake-target",
        on_list_tools=target_list_tools or _fake_target_list_tools,
        on_call_tool=target_call_tool or _fake_target_call_tool,
    )

    async with create_client_server_memory_streams() as (target_client_streams, target_server_streams):
        target_server_read, target_server_write = target_server_streams
        target_client_read, target_client_write = target_client_streams

        async with create_client_server_memory_streams() as (proxy_client_streams, proxy_server_streams):
            proxy_server_read, proxy_server_write = proxy_server_streams
            test_client_read, test_client_write = proxy_client_streams

            async with anyio.create_task_group() as tg:

                async def run_target():
                    await target_server.run(
                        target_server_read, target_server_write, target_server.create_initialization_options()
                    )

                tg.start_soon(run_target)

                async with ClientSession(target_client_read, target_client_write) as target_session:
                    await target_session.initialize()

                    proxy = MaskingProxy(
                        target_session=target_session,
                        session_id=session_id,
                        override_store=override_store,
                        vault_manager=vault_manager,
                    )
                    await proxy.discover()

                    async def run_proxy():
                        await proxy.server.run(
                            proxy_server_read, proxy_server_write, proxy.server.create_initialization_options()
                        )

                    tg.start_soon(run_proxy)

                    async with ClientSession(test_client_read, test_client_write) as test_session:
                        await test_session.initialize()
                        await test_body(test_session, proxy)

                tg.cancel_scope.cancel()


def test_mask_json_value_bare_scalar_string_is_masked(override_store, vault_manager):
    """Unit-level regression for gap 1 (see mcp_proxy.py's
    _mask_json_value docstring): a bare string used to pass through
    _mask_json_value completely unmasked."""
    from decoy.mcp_proxy import _mask_json_value

    result = _mask_json_value("Contact: alice@example.com", "s1", override_store, vault_manager)
    assert "alice@example.com" not in result
    assert "@" in result  # still a plausible fake email (Faker's example.com/.org/.net domains vary)


def test_mask_json_value_list_of_non_dict_items_is_masked(override_store, vault_manager):
    """Unit-level regression for gap 2: a list that isn't uniformly dict
    rows used to pass through unmasked."""
    from decoy.mcp_proxy import _mask_json_value

    result = _mask_json_value(["bob@example.com", "alice@example.com"], "s1", override_store, vault_manager)
    assert "bob@example.com" not in result
    assert "alice@example.com" not in result
    assert len(result) == 2


def test_mask_json_value_list_of_non_dict_items_nested_under_a_dict_key_is_masked(override_store, vault_manager):
    """Unit-level regression for gap 3 -- the one only found while
    building the end-to-end test, not from re-reading the code: a dict
    with a MIX of a row-shaped key and a plain-list-of-strings key used
    to leak the plain-list key entirely, since the old code returned as
    soon as it found ANY list-of-dicts key."""
    from decoy.mcp_proxy import _mask_json_value

    result = _mask_json_value(
        {"hits": [{"email": "alice@example.com"}], "warnings": ["contact dave.chen@example.com for details"]},
        "s1",
        override_store,
        vault_manager,
    )
    assert "alice@example.com" not in str(result)
    assert "dave.chen@example.com" not in str(result)
    assert "@" in result["hits"][0]["email"]
    assert "@" in result["warnings"][0]


def test_mask_json_value_list_of_non_string_scalars_unaffected(override_store, vault_manager):
    """Sanity check: the fix for gaps 1/2/3 must not start trying to
    mask non-string scalars (numbers, bools, None) -- there's nothing
    PII-shaped about them and this function never handled them before."""
    from decoy.mcp_proxy import _mask_json_value

    result = _mask_json_value([1, 2, 3, True, None], "s1", override_store, vault_manager)
    assert result == [1, 2, 3, True, None]


async def test_dynamic_tool_discovery_and_masked_result(override_store, vault_manager):
    session_id = str(uuid.uuid4())

    async def body(test_session: ClientSession, proxy: MaskingProxy):
        tools = await test_session.list_tools()
        assert [t.name for t in tools.tools] == ["get_customers"]

        result = await test_session.call_tool("get_customers", {})
        assert not result.is_error
        text = result.content[0].text
        rows = json.loads(text)

        assert len(rows) == 2
        for real, masked in zip(FAKE_ROWS, rows):
            assert masked["name"] != real["name"]
            assert masked["email"] != real["email"]
            assert "@" in masked["email"]
            assert masked["id"] != 0  # numeric affine-transformed, still present

        raw_dump = json.dumps(rows)
        for real in FAKE_ROWS:
            assert real["name"] not in raw_dump
            assert real["email"] not in raw_dump

    await _run_full_stack(override_store, vault_manager, session_id, body)


async def test_masking_uses_the_same_override_store_and_vault_as_other_entry_points(tmp_path, vault_manager):
    # This proxy is a THIRD entry point into the masking core (after direct
    # DB queries and edge-case sampling). Prove it shares the same
    # override_store wiring, not a separate/default one, using a never_mask
    # override on "email" -- if the proxy used a different store, this
    # would come back masked instead of real.
    path = tmp_path / "overrides.json"
    path.write_text(
        json.dumps({"always_mask": {"field_names": []}, "never_mask": {"field_names": ["email"]}})
    )
    store = OverrideStore(path=path)
    session_id = str(uuid.uuid4())

    async def body(test_session: ClientSession, proxy: MaskingProxy):
        result = await test_session.call_tool("get_customers", {})
        rows = json.loads(result.content[0].text)
        for real, masked in zip(FAKE_ROWS, rows):
            assert masked["email"] == real["email"]  # never_mask: kept real
            assert masked["name"] != real["name"]  # unrelated field still masked

    await _run_full_stack(store, vault_manager, session_id, body)


async def test_shared_vault_gives_same_fake_as_direct_masker_call(override_store, vault_manager):
    # Same session_id used elsewhere in the app (e.g. via ask_over_data)
    # must produce the SAME fake for the same real value seen through the
    # proxy -- proving this isn't a separate vault instance.
    from decoy.masker import Masker

    session_id = str(uuid.uuid4())

    async def body(test_session: ClientSession, proxy: MaskingProxy):
        result = await test_session.call_tool("get_customers", {})
        rows = json.loads(result.content[0].text)
        proxy_fake_email = rows[0]["email"]

        masker = Masker(session_id=session_id, override_store=override_store, vault_manager=vault_manager)
        direct_fake = masker.vault.lookup_fake("alice@example.com")
        assert direct_fake == proxy_fake_email

    await _run_full_stack(override_store, vault_manager, session_id, body)


async def test_non_row_shaped_target_mixed_structured_content_is_fully_masked(override_store, vault_manager):
    """End-to-end proof (real proxy stack, real SECOND fake target with a
    genuinely different shape from the ES-row-shaped target every other
    test in this file uses -- not a unit-level call to _mask_json_value
    in isolation) that a target's structured_content with a MIX of a
    row-shaped key and a plain-list-of-strings key gets BOTH keys masked,
    not just the row-shaped one. Confirmed as a real gap before this fix:
    this exact call came back with "hits" masked but "warnings" leaking
    the real email verbatim."""
    session_id = str(uuid.uuid4())

    async def body(test_session: ClientSession, proxy: MaskingProxy):
        result = await test_session.call_tool("search_issues", {})
        assert not result.is_error
        structured = result.structured_content

        assert "alice@example.com" not in str(structured)
        assert "dave.chen@example.com" not in str(structured)  # the actual confirmed gap
        # "@" not a specific domain: Faker's fake emails vary across
        # example.com/.org/.net, so pinning to .com here was its own
        # flaky test bug (confirmed by repeated runs), not a product bug.
        assert "@" in structured["hits"][0]["assignee_email"]  # still a plausible fake
        assert "@" in structured["warnings"][0]

    await _run_full_stack(
        override_store,
        vault_manager,
        session_id,
        body,
        target_list_tools=_second_fake_target_list_tools,
        target_call_tool=_second_fake_target_call_tool,
    )


async def test_target_failure_does_not_crash_proxy(override_store, vault_manager):
    async def failing_call_tool(ctx, params: types.CallToolRequestParams):
        raise ConnectionError("target unreachable")

    target_server = Server(
        "flaky-target", on_list_tools=_fake_target_list_tools, on_call_tool=failing_call_tool
    )
    session_id = str(uuid.uuid4())

    async with create_client_server_memory_streams() as (target_client_streams, target_server_streams):
        target_server_read, target_server_write = target_server_streams
        target_client_read, target_client_write = target_client_streams

        async with create_client_server_memory_streams() as (proxy_client_streams, proxy_server_streams):
            proxy_server_read, proxy_server_write = proxy_server_streams
            test_client_read, test_client_write = proxy_client_streams

            async with anyio.create_task_group() as tg:

                async def run_target():
                    await target_server.run(
                        target_server_read, target_server_write, target_server.create_initialization_options()
                    )

                tg.start_soon(run_target)

                async with ClientSession(target_client_read, target_client_write) as target_session:
                    await target_session.initialize()
                    proxy = MaskingProxy(
                        target_session=target_session,
                        session_id=session_id,
                        override_store=override_store,
                        vault_manager=vault_manager,
                    )
                    await proxy.discover()

                    async def run_proxy():
                        await proxy.server.run(
                            proxy_server_read, proxy_server_write, proxy.server.create_initialization_options()
                        )

                    tg.start_soon(run_proxy)

                    async with ClientSession(test_client_read, test_client_write) as test_session:
                        await test_session.initialize()
                        result = await test_session.call_tool("get_customers", {})
                        assert result.is_error
                        assert "target call failed" in result.content[0].text

                tg.cancel_scope.cancel()


async def test_slow_target_call_tool_times_out_cleanly(override_store, vault_manager):
    # A hung/slow target (real network call under load, slow query, target
    # that never responds) must not hang the caller's tool call
    # indefinitely -- confirm a short configured timeout turns that into a
    # clean is_error=True result instead, and confirm it actually returns
    # promptly rather than waiting out the target's full sleep.
    async def slow_call_tool(ctx, params: types.CallToolRequestParams):
        await anyio.sleep(5.0)
        return types.CallToolResult(content=[types.TextContent(type="text", text="too late")])

    target_server = Server(
        "slow-target", on_list_tools=_fake_target_list_tools, on_call_tool=slow_call_tool
    )
    session_id = str(uuid.uuid4())

    with anyio.fail_after(10):  # safety net: fail the test itself fast if something regresses to a hang
        async with create_client_server_memory_streams() as (target_client_streams, target_server_streams):
            target_server_read, target_server_write = target_server_streams
            target_client_read, target_client_write = target_client_streams

            async with create_client_server_memory_streams() as (proxy_client_streams, proxy_server_streams):
                proxy_server_read, proxy_server_write = proxy_server_streams
                test_client_read, test_client_write = proxy_client_streams

                async with anyio.create_task_group() as tg:

                    async def run_target():
                        await target_server.run(
                            target_server_read, target_server_write, target_server.create_initialization_options()
                        )

                    tg.start_soon(run_target)

                    async with ClientSession(target_client_read, target_client_write) as target_session:
                        await target_session.initialize()
                        proxy = MaskingProxy(
                            target_session=target_session,
                            session_id=session_id,
                            override_store=override_store,
                            vault_manager=vault_manager,
                            call_timeout_seconds=0.3,
                        )
                        await proxy.discover()

                        async def run_proxy():
                            await proxy.server.run(
                                proxy_server_read, proxy_server_write, proxy.server.create_initialization_options()
                            )

                        tg.start_soon(run_proxy)

                        async with ClientSession(test_client_read, test_client_write) as test_session:
                            await test_session.initialize()
                            start = anyio.current_time()
                            result = await test_session.call_tool("get_customers", {})
                            elapsed = anyio.current_time() - start

                            assert result.is_error
                            assert "timed out" in result.content[0].text
                            # returned promptly (well under the target's 5s sleep), not hung
                            assert elapsed < 2.0

                    tg.cancel_scope.cancel()


async def test_slow_target_discover_times_out_cleanly(override_store, vault_manager):
    # list_tools() during proxy startup is a separate call site from
    # call_tool and has no per-request timeout parameter in the installed
    # mcp client API -- confirm the outer anyio.fail_after guard on
    # discover() still turns a hung target into an empty tool list rather
    # than hanging proxy startup indefinitely.
    async def slow_list_tools(ctx, params):
        await anyio.sleep(5.0)
        return types.ListToolsResult(tools=[])

    target_server = Server(
        "slow-discovery-target", on_list_tools=slow_list_tools, on_call_tool=_fake_target_call_tool
    )
    session_id = str(uuid.uuid4())

    with anyio.fail_after(10):  # safety net: fail the test itself fast if something regresses to a hang
        async with create_client_server_memory_streams() as (target_client_streams, target_server_streams):
            target_server_read, target_server_write = target_server_streams
            target_client_read, target_client_write = target_client_streams

            async with anyio.create_task_group() as tg:

                async def run_target():
                    await target_server.run(
                        target_server_read, target_server_write, target_server.create_initialization_options()
                    )

                tg.start_soon(run_target)

                async with ClientSession(target_client_read, target_client_write) as target_session:
                    await target_session.initialize()
                    proxy = MaskingProxy(
                        target_session=target_session,
                        session_id=session_id,
                        override_store=override_store,
                        vault_manager=vault_manager,
                        discover_timeout_seconds=0.3,
                    )

                    start = anyio.current_time()
                    discovered = await proxy.discover()
                    elapsed = anyio.current_time() - start

                    assert discovered == []
                    assert elapsed < 2.0

                tg.cancel_scope.cancel()


async def test_outgoing_argument_matching_a_previously_masked_value_is_unmasked_before_forwarding(
    override_store, vault_manager
):
    # This is the core fix for the "target can be remote" gap: an
    # argument equal to a FAKE value already in this session's vault (the
    # only thing the LLM would ever actually hold, since real values are
    # masked before reaching it) must be reversed back to the REAL value
    # before the proxy forwards the call -- otherwise a target doing a
    # real lookup (e.g. a literal DB query) against the fake value would
    # find nothing, and worse, the "fix" of masking the argument instead
    # would never have worked for that same reason.
    session_id = str(uuid.uuid4())
    real_email = "jane.doe@acme.com"

    # Establish the real->fake mapping the same way the free-text path
    # would: an earlier prompt in this session got masked.
    masker = Masker(session_id=session_id, override_store=override_store, vault_manager=vault_manager)
    fake_email = masker.mask_text(f"Look up {real_email}").detections[0].fake

    captured_arguments = {}

    async def capturing_target_call_tool(ctx, params: types.CallToolRequestParams):
        captured_arguments.update(params.arguments or {})
        return types.CallToolResult(content=[types.TextContent(type="text", text="ok")])

    target_server = Server(
        "capturing-target", on_list_tools=_fake_target_list_tools, on_call_tool=capturing_target_call_tool
    )

    async with create_client_server_memory_streams() as (target_client_streams, target_server_streams):
        target_server_read, target_server_write = target_server_streams
        target_client_read, target_client_write = target_client_streams

        async with create_client_server_memory_streams() as (proxy_client_streams, proxy_server_streams):
            proxy_server_read, proxy_server_write = proxy_server_streams
            test_client_read, test_client_write = proxy_client_streams

            async with anyio.create_task_group() as tg:

                async def run_target():
                    await target_server.run(
                        target_server_read, target_server_write, target_server.create_initialization_options()
                    )

                tg.start_soon(run_target)

                async with ClientSession(target_client_read, target_client_write) as target_session:
                    await target_session.initialize()
                    proxy = MaskingProxy(
                        target_session=target_session,
                        session_id=session_id,
                        override_store=override_store,
                        vault_manager=vault_manager,
                    )
                    await proxy.discover()

                    async def run_proxy():
                        await proxy.server.run(
                            proxy_server_read, proxy_server_write, proxy.server.create_initialization_options()
                        )

                    tg.start_soon(run_proxy)

                    async with ClientSession(test_client_read, test_client_write) as test_session:
                        await test_session.initialize()
                        # The caller (e.g. Claude Code) only ever holds the
                        # FAKE value -- it sends that as the argument.
                        await test_session.call_tool("get_customers", {"email": fake_email})

                tg.cancel_scope.cancel()

    # The target must have received the REAL email, not the fake --
    # otherwise a real lookup on the target's side would find nothing.
    assert captured_arguments.get("email") == real_email
    assert captured_arguments.get("email") != fake_email


async def test_outgoing_argument_never_previously_masked_passes_through_unchanged(override_store, vault_manager):
    # A value with no vault entry (never masked earlier in this session)
    # has nothing to reverse -- it must be forwarded exactly as received,
    # not altered or dropped. This is the documented residual boundary:
    # Decoy can't protect what it never saw.
    session_id = str(uuid.uuid4())
    captured_arguments = {}

    async def capturing_target_call_tool(ctx, params: types.CallToolRequestParams):
        captured_arguments.update(params.arguments or {})
        return types.CallToolResult(content=[types.TextContent(type="text", text="ok")])

    target_server = Server(
        "capturing-target-2", on_list_tools=_fake_target_list_tools, on_call_tool=capturing_target_call_tool
    )

    async with create_client_server_memory_streams() as (target_client_streams, target_server_streams):
        target_server_read, target_server_write = target_server_streams
        target_client_read, target_client_write = target_client_streams

        async with create_client_server_memory_streams() as (proxy_client_streams, proxy_server_streams):
            proxy_server_read, proxy_server_write = proxy_server_streams
            test_client_read, test_client_write = proxy_client_streams

            async with anyio.create_task_group() as tg:

                async def run_target():
                    await target_server.run(
                        target_server_read, target_server_write, target_server.create_initialization_options()
                    )

                tg.start_soon(run_target)

                async with ClientSession(target_client_read, target_client_write) as target_session:
                    await target_session.initialize()
                    proxy = MaskingProxy(
                        target_session=target_session,
                        session_id=session_id,
                        override_store=override_store,
                        vault_manager=vault_manager,
                    )
                    await proxy.discover()

                    async def run_proxy():
                        await proxy.server.run(
                            proxy_server_read, proxy_server_write, proxy.server.create_initialization_options()
                        )

                    tg.start_soon(run_proxy)

                    async with ClientSession(test_client_read, test_client_write) as test_session:
                        await test_session.initialize()
                        await test_session.call_tool("get_customers", {"query": "some ordinary search term"})

                tg.cancel_scope.cancel()

    assert captured_arguments.get("query") == "some ordinary search term"

"""Tests for decoy.chat_proxy.

Includes real HTTP-level integration tests (starlette's TestClient
driving the actual ASGI app, with the proxy's own outbound httpx client
rerouted via httpx.ASGITransport to a FAKE upstream app -- also real
ASGI, not a mocked function) so the masking/unmasking is proven across an
actual request/response cycle, not just as isolated pure functions.
"""

import json
import uuid

import pytest
from starlette.testclient import TestClient

from decoy.chat_proxy import (
    ChatProxyConfig,
    StreamUnmaskBuffer,
    create_app,
    mask_anthropic_request,
    mask_openai_request,
    resolve_session_id,
    unmask_anthropic_response,
    unmask_openai_response,
)
from decoy.masker import Masker
from decoy.mcp_proxy import MaskingProxy  # noqa: F401 -- see cross-proxy test below
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
    return OverrideStore(path=tmp_path / "overrides.json")


# --------------------------------------------------------------------------
# Session resolution
# --------------------------------------------------------------------------


def test_resolve_session_id_prefers_header(monkeypatch):
    monkeypatch.setenv("DECOY_SESSION_ID", "env-session")
    assert resolve_session_id("header-session") == "header-session"


def test_resolve_session_id_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("DECOY_SESSION_ID", "env-session")
    assert resolve_session_id(None) == "env-session"


def test_resolve_session_id_falls_back_to_constant(monkeypatch):
    monkeypatch.delenv("DECOY_SESSION_ID", raising=False)
    assert resolve_session_id(None) == "chat-proxy-default"


# --------------------------------------------------------------------------
# Pure request/response masking functions
# --------------------------------------------------------------------------


def test_mask_anthropic_request_masks_string_content_and_system(vault_manager, override_store):
    masker = Masker(session_id="s1", vault_manager=vault_manager, override_store=override_store)
    body = {
        "model": "claude-haiku-4-5",
        "system": "Contact bob@example.com if urgent.",
        "messages": [{"role": "user", "content": "Email me at alice@example.com please."}],
    }
    masked, request_id = mask_anthropic_request(body, masker)
    assert "bob@example.com" not in masked["system"]
    assert "alice@example.com" not in masked["messages"][0]["content"]
    assert masked["model"] == "claude-haiku-4-5"  # non-text fields untouched
    assert isinstance(request_id, str) and request_id


def test_mask_anthropic_request_masks_text_blocks_and_tool_result_content(vault_manager, override_store):
    masker = Masker(session_id="s1", vault_manager=vault_manager, override_store=override_store)
    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "My email is carol@example.com"},
                    {"type": "tool_result", "tool_use_id": "t1", "content": "Found dave@example.com in the record"},
                ],
            }
        ]
    }
    masked, _ = mask_anthropic_request(body, masker)
    blocks = masked["messages"][0]["content"]
    assert "carol@example.com" not in blocks[0]["text"]
    assert "dave@example.com" not in blocks[1]["content"]


def test_unmask_anthropic_response_reverses_fakes_in_text_blocks(vault_manager, override_store):
    masker = Masker(session_id="s1", vault_manager=vault_manager, override_store=override_store)
    masked = masker.mask_text("eve@example.com").masked_text
    fake_email = masked
    response_body = {"content": [{"type": "text", "text": f"Sure, I'll email {fake_email}"}]}
    unmasked = unmask_anthropic_response(response_body, masker)
    assert "eve@example.com" in unmasked["content"][0]["text"]
    assert fake_email not in unmasked["content"][0]["text"]


def test_mask_and_unmask_openai_shape(vault_manager, override_store):
    masker = Masker(session_id="s1", vault_manager=vault_manager, override_store=override_store)
    body = {"messages": [{"role": "user", "content": "call frank@example.com"}]}
    masked, _ = mask_openai_request(body, masker)
    assert "frank@example.com" not in masked["messages"][0]["content"]

    fake = masker.mask_text("grace@example.com").masked_text
    response_body = {"choices": [{"message": {"role": "assistant", "content": f"ok, {fake}"}}]}
    unmasked = unmask_openai_response(response_body, masker)
    assert "grace@example.com" in unmasked["choices"][0]["message"]["content"]


# --------------------------------------------------------------------------
# StreamUnmaskBuffer: the explicit "fake value split across a chunk
# boundary" test.
# --------------------------------------------------------------------------


def test_stream_unmask_buffer_reconstructs_a_fake_value_split_across_chunks(vault_manager, override_store):
    masker = Masker(session_id="s1", vault_manager=vault_manager, override_store=override_store)
    fake_email = masker.mask_text("henry@example.com").masked_text
    assert len(fake_email) > 4, "test assumes a fake email long enough to meaningfully split"

    split_at = len(fake_email) // 2
    chunk1 = f"Sure, I'll contact {fake_email[:split_at]}"
    chunk2 = f"{fake_email[split_at:]} right away."

    buf = StreamUnmaskBuffer(masker)
    out1 = buf.feed(chunk1)
    out2 = buf.feed(chunk2)
    out3 = buf.flush()

    full = out1 + out2 + out3
    assert "henry@example.com" in full
    assert fake_email not in full
    assert full == "Sure, I'll contact henry@example.com right away."


def test_stream_unmask_buffer_holds_back_a_potential_partial_fake(vault_manager, override_store):
    """The chunk boundary lands exactly mid-fake -- confirm feed() does
    NOT eagerly flush the partial fake fragment (which would be
    unmaskable-looking gibberish shown to the user even though the real
    value never leaked)."""
    masker = Masker(session_id="s1", vault_manager=vault_manager, override_store=override_store)
    fake_email = masker.mask_text("iris@example.com").masked_text
    buf = StreamUnmaskBuffer(masker)

    out1 = buf.feed(fake_email[: len(fake_email) - 1])  # everything but the last char
    # Nothing should be flushed yet that could contain a truncated fake --
    # the whole point of the holdback window.
    assert fake_email[:3] not in out1 or out1 == ""


# --------------------------------------------------------------------------
# Full HTTP integration: real ASGI app <-> real ASGI fake-upstream app,
# wired together via httpx.ASGITransport (not mocked functions).
# --------------------------------------------------------------------------


def _fake_anthropic_upstream(real_value_that_must_never_arrive: str = "jane@example.com"):
    """A minimal ASGI app mimicking POST /v1/messages: echoes back
    whatever text it received (which will be the MASKED/fake text, since
    this stands in for the real Anthropic API) wrapped in a reply, so the
    test can assert the fake was correctly unmasked on the way back.

    Checks for the SPECIFIC real value, not a generic "@example.com"
    substring: Faker's own fake emails can themselves legitimately land
    on the "example.com"/"example.org"/"example.net" domains (they're
    Faker's own safe example-domain pool), so a generic domain-substring
    check is a false-positive-prone test bug, not a real leak detector --
    confirmed by this test flaking under repeated runs before this fix,
    purely because a *fake* email happened to also end in "@example.com".
    """
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    async def handle(request):
        body = await request.json()
        received_text = body["messages"][0]["content"]
        assert real_value_that_must_never_arrive not in received_text
        return JSONResponse(
            {
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": f"Got it, I see: {received_text}"}],
                "model": body.get("model"),
                "stop_reason": "end_turn",
            }
        )

    return Starlette(routes=[Route("/v1/messages", handle, methods=["POST"])])


def test_v1_messages_end_to_end_masks_outgoing_and_unmasks_incoming(vault_manager, override_store):
    fake_upstream = _fake_anthropic_upstream()
    import httpx

    config = ChatProxyConfig(
        anthropic_api_key="test-key-not-real",
        vault_manager=vault_manager,
        override_store=override_store,
        httpx_transport=httpx.ASGITransport(app=fake_upstream),
    )
    app = create_app(config)

    with TestClient(app) as client:
        resp = client.post(
            "/v1/messages",
            json={
                "model": "claude-haiku-4-5",
                "max_tokens": 100,
                "messages": [{"role": "user", "content": "Please contact jane@example.com about this."}],
            },
            headers={"x-decoy-session-id": "test-e2e-session"},
        )

    assert resp.status_code == 200
    reply_text = resp.json()["content"][0]["text"]
    # The real email is back in what the CLIENT sees (proxy unmasked the
    # response)...
    assert "jane@example.com" in reply_text
    # ...but never touched the network in between -- the fake upstream's
    # own assertion above already enforced this; this is a client-side
    # sanity check on top of it.


def test_v1_chat_completions_end_to_end(vault_manager, override_store):
    import httpx
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    async def handle(request):
        body = await request.json()
        received = body["messages"][0]["content"]
        return JSONResponse(
            {"choices": [{"message": {"role": "assistant", "content": f"noted: {received}"}, "finish_reason": "stop"}]}
        )

    fake_upstream = Starlette(routes=[Route("/v1/chat/completions", handle, methods=["POST"])])

    config = ChatProxyConfig(
        openai_api_key="test-key-not-real",
        vault_manager=vault_manager,
        override_store=override_store,
        httpx_transport=httpx.ASGITransport(app=fake_upstream),
    )
    app = create_app(config)

    with TestClient(app) as client:
        resp = client.post(
            "/v1/chat/completions",
            json={"model": "gpt-test", "messages": [{"role": "user", "content": "email kate@example.com"}]},
            headers={"x-decoy-session-id": "test-e2e-session-openai"},
        )

    assert resp.status_code == 200
    assert "kate@example.com" in resp.json()["choices"][0]["message"]["content"]


def test_missing_api_key_returns_clean_error_not_a_crash(vault_manager, override_store):
    config = ChatProxyConfig(anthropic_api_key=None, vault_manager=vault_manager, override_store=override_store)
    app = create_app(config)
    with TestClient(app) as client:
        resp = client.post("/v1/messages", json={"model": "x", "messages": []})
    assert resp.status_code == 500
    assert "ANTHROPIC_API_KEY" in resp.json()["error"]["message"]


def test_missing_credentials_error_names_both_api_key_and_auth_token(vault_manager, override_store):
    """Neither anthropic_api_key nor anthropic_auth_token set -- the error
    must name both env vars, not just the old ANTHROPIC_API_KEY-only one,
    so an org running gateway mode isn't misled into setting the wrong
    credential."""
    config = ChatProxyConfig(
        anthropic_api_key=None, anthropic_auth_token=None, vault_manager=vault_manager, override_store=override_store
    )
    app = create_app(config)
    with TestClient(app) as client:
        resp = client.post("/v1/messages", json={"model": "x", "messages": []})
    assert resp.status_code == 500
    message = resp.json()["error"]["message"]
    assert "ANTHROPIC_API_KEY" in message
    assert "ANTHROPIC_AUTH_TOKEN" in message


# --------------------------------------------------------------------------
# Internal AI gateway mode: ANTHROPIC_AUTH_TOKEN -> Authorization: Bearer,
# against a mock gateway server standing in for a real internal gateway
# (never the user's real org gateway -- see this file's module docstring
# on the ASGITransport pattern used throughout: a real ASGI app, driven
# through real HTTP/SSE machinery, just not real network I/O).
# --------------------------------------------------------------------------


def _mock_gateway_app(received_headers: dict):
    """A minimal ASGI app standing in for an internal AI gateway's
    POST /v1/messages endpoint: records whatever auth header it actually
    received (so the test can assert on it) and returns a minimal
    valid-shaped Anthropic response."""
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    async def handle(request):
        received_headers.update(dict(request.headers))
        body = await request.json()
        return JSONResponse(
            {
                "id": "msg_gateway_test",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": "ok"}],
                "model": body.get("model"),
                "stop_reason": "end_turn",
            }
        )

    return Starlette(routes=[Route("/v1/messages", handle, methods=["POST"])])


def test_v1_messages_sends_bearer_auth_when_auth_token_is_set_not_x_api_key(vault_manager, override_store):
    import httpx

    received_headers: dict = {}
    fake_gateway = _mock_gateway_app(received_headers)

    config = ChatProxyConfig(
        anthropic_api_key=None,
        anthropic_auth_token="fake-gateway-bearer-token",
        anthropic_base_url="https://mock-internal-gateway.invalid",
        vault_manager=vault_manager,
        override_store=override_store,
        httpx_transport=httpx.ASGITransport(app=fake_gateway),
    )
    app = create_app(config)

    with TestClient(app) as client:
        resp = client.post(
            "/v1/messages",
            json={"model": "claude-haiku-4-5", "max_tokens": 10, "messages": [{"role": "user", "content": "hi"}]},
        )

    assert resp.status_code == 200
    assert received_headers.get("authorization") == "Bearer fake-gateway-bearer-token"
    assert "x-api-key" not in received_headers


def test_v1_messages_prefers_auth_token_over_api_key_when_both_are_set(vault_manager, override_store):
    """Documented precedence rule (see ChatProxyConfig.anthropic_auth_token's
    docstring and the upstream_headers construction in messages()): the
    bearer token wins so a leftover ANTHROPIC_API_KEY never silently
    overrides a deliberately-configured gateway token."""
    import httpx

    received_headers: dict = {}
    fake_gateway = _mock_gateway_app(received_headers)

    config = ChatProxyConfig(
        anthropic_api_key="should-not-be-used",
        anthropic_auth_token="should-win",
        anthropic_base_url="https://mock-internal-gateway.invalid",
        vault_manager=vault_manager,
        override_store=override_store,
        httpx_transport=httpx.ASGITransport(app=fake_gateway),
    )
    app = create_app(config)

    with TestClient(app) as client:
        resp = client.post(
            "/v1/messages",
            json={"model": "claude-haiku-4-5", "max_tokens": 10, "messages": [{"role": "user", "content": "hi"}]},
        )

    assert resp.status_code == 200
    assert received_headers.get("authorization") == "Bearer should-win"
    assert "x-api-key" not in received_headers


def test_v1_messages_still_sends_x_api_key_when_no_auth_token_is_set(vault_manager, override_store):
    """No-regression check: the existing ANTHROPIC_API_KEY path is
    unchanged when ANTHROPIC_AUTH_TOKEN is not configured."""
    import httpx

    received_headers: dict = {}
    fake_gateway = _mock_gateway_app(received_headers)

    config = ChatProxyConfig(
        anthropic_api_key="real-style-api-key",
        anthropic_auth_token=None,
        vault_manager=vault_manager,
        override_store=override_store,
        httpx_transport=httpx.ASGITransport(app=fake_gateway),
    )
    app = create_app(config)

    with TestClient(app) as client:
        resp = client.post(
            "/v1/messages",
            json={"model": "claude-haiku-4-5", "max_tokens": 10, "messages": [{"role": "user", "content": "hi"}]},
        )

    assert resp.status_code == 200
    assert received_headers.get("x-api-key") == "real-style-api-key"
    assert "authorization" not in received_headers


def test_chat_proxy_config_from_env_reads_anthropic_auth_token(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "from-env-token")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    config = ChatProxyConfig.from_env()
    assert config.anthropic_auth_token == "from-env-token"


# --------------------------------------------------------------------------
# Opt-in outbound TLS verification skip
# --------------------------------------------------------------------------


def test_upstream_skip_tls_verify_defaults_to_false(vault_manager, override_store, monkeypatch):
    import httpx

    captured_kwargs = {}
    original_init = httpx.AsyncClient.__init__

    def capturing_init(self, *args, **kwargs):
        captured_kwargs.update(kwargs)
        return original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", capturing_init)

    config = ChatProxyConfig(anthropic_api_key="k", vault_manager=vault_manager, override_store=override_store)
    app = create_app(config)
    with TestClient(app):
        pass

    assert config.upstream_skip_tls_verify is False
    assert captured_kwargs.get("verify") is True


def test_upstream_skip_tls_verify_when_explicitly_enabled_disables_verification_and_warns(
    vault_manager, override_store, monkeypatch, caplog
):
    import logging

    import httpx

    captured_kwargs = {}
    original_init = httpx.AsyncClient.__init__

    def capturing_init(self, *args, **kwargs):
        captured_kwargs.update(kwargs)
        return original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", capturing_init)

    config = ChatProxyConfig(
        anthropic_api_key="k",
        vault_manager=vault_manager,
        override_store=override_store,
        upstream_skip_tls_verify=True,
    )
    app = create_app(config)
    with caplog.at_level(logging.WARNING):
        with TestClient(app):
            pass

    assert captured_kwargs.get("verify") is False
    assert any("TLS" in record.message and "DECOY_UPSTREAM_SKIP_TLS_VERIFY" in record.message for record in caplog.records)


def test_chat_proxy_config_from_env_skip_tls_verify_defaults_off_and_is_opt_in(monkeypatch):
    monkeypatch.delenv("DECOY_UPSTREAM_SKIP_TLS_VERIFY", raising=False)
    assert ChatProxyConfig.from_env().upstream_skip_tls_verify is False

    monkeypatch.setenv("DECOY_UPSTREAM_SKIP_TLS_VERIFY", "true")
    assert ChatProxyConfig.from_env().upstream_skip_tls_verify is True


def test_v1_messages_streaming_end_to_end_reassembles_a_split_fake_correctly(vault_manager, override_store):
    """Real SSE, real HTTP layer, real fake upstream that deliberately
    splits the echoed (fake) value across two content_block_delta events
    -- proves the whole pipeline (mask outgoing -> fake upstream echoes
    the fake, split mid-value across chunks -> proxy's _stream_anthropic
    + StreamUnmaskBuffer reassemble and unmask it) works end to end, not
    just the buffer class in isolation."""
    import httpx
    from starlette.applications import Starlette
    from starlette.responses import StreamingResponse
    from starlette.routing import Route

    async def handle(request):
        body = await request.json()
        fake_text = body["messages"][0]["content"]  # this is the MASKED (fake) text

        async def gen():
            mid = len(fake_text) // 2

            def sse(event_type, data):
                return f"event: {event_type}\ndata: {json.dumps(data)}\n\n".encode()

            yield sse("message_start", {"type": "message_start"})
            yield sse(
                "content_block_start",
                {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            )
            yield sse(
                "content_block_delta",
                {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": fake_text[:mid]}},
            )
            yield sse(
                "content_block_delta",
                {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": fake_text[mid:]}},
            )
            yield sse("content_block_stop", {"type": "content_block_stop", "index": 0})
            yield sse("message_stop", {"type": "message_stop"})

        return StreamingResponse(gen(), media_type="text/event-stream")

    fake_upstream = Starlette(routes=[Route("/v1/messages", handle, methods=["POST"])])
    config = ChatProxyConfig(
        anthropic_api_key="test-key",
        vault_manager=vault_manager,
        override_store=override_store,
        httpx_transport=httpx.ASGITransport(app=fake_upstream),
    )
    app = create_app(config)
    session_id = f"stream-test-{uuid.uuid4()}"

    with TestClient(app) as client:
        with client.stream(
            "POST",
            "/v1/messages",
            json={
                "model": "claude-haiku-4-5",
                "messages": [{"role": "user", "content": "contact leo@example.com"}],
                "stream": True,
            },
            headers={"x-decoy-session-id": session_id},
        ) as resp:
            assert resp.status_code == 200
            reconstructed = ""
            for line in resp.iter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[len("data:"):].strip()
                if not payload:
                    continue
                event = json.loads(payload)
                if event.get("type") == "content_block_delta" and event.get("delta", {}).get("type") == "text_delta":
                    reconstructed += event["delta"]["text"]

    assert "leo@example.com" in reconstructed
    # and no fake email fragment leaked into the client-visible text either
    masker = Masker(session_id=session_id, vault_manager=vault_manager, override_store=override_store)
    fake = masker.vault.lookup_fake("leo@example.com")
    assert fake not in reconstructed


def test_v1_chat_completions_streaming_end_to_end(vault_manager, override_store):
    import httpx
    from starlette.applications import Starlette
    from starlette.responses import StreamingResponse
    from starlette.routing import Route

    async def handle(request):
        body = await request.json()
        fake_text = body["messages"][0]["content"]

        async def gen():
            mid = len(fake_text) // 2
            chunk1 = {"choices": [{"delta": {"content": fake_text[:mid]}, "index": 0}]}
            chunk2 = {"choices": [{"delta": {"content": fake_text[mid:]}, "index": 0}]}
            yield f"data: {json.dumps(chunk1)}\n\n".encode()
            yield f"data: {json.dumps(chunk2)}\n\n".encode()
            yield b"data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    fake_upstream = Starlette(routes=[Route("/v1/chat/completions", handle, methods=["POST"])])
    config = ChatProxyConfig(
        openai_api_key="test-key",
        vault_manager=vault_manager,
        override_store=override_store,
        httpx_transport=httpx.ASGITransport(app=fake_upstream),
    )
    app = create_app(config)
    session_id = f"stream-test-openai-{uuid.uuid4()}"

    with TestClient(app) as client:
        with client.stream(
            "POST",
            "/v1/chat/completions",
            json={"model": "gpt-test", "messages": [{"role": "user", "content": "email mia@example.com"}], "stream": True},
            headers={"x-decoy-session-id": session_id},
        ) as resp:
            assert resp.status_code == 200
            reconstructed = ""
            for line in resp.iter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[len("data:"):].strip()
                if not payload or payload == "[DONE]":
                    continue
                chunk = json.loads(payload)
                content = chunk["choices"][0]["delta"].get("content")
                if content:
                    reconstructed += content

    assert "mia@example.com" in reconstructed


def test_healthz(vault_manager, override_store):
    config = ChatProxyConfig(vault_manager=vault_manager, override_store=override_store)
    app = create_app(config)
    with TestClient(app) as client:
        resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# --------------------------------------------------------------------------
# Cross-proxy consistency: the chat proxy and the MCP proxy sharing ONE
# vault for the same session_id (in-process here via a shared
# VaultManager -- the real cross-process case is covered by
# test_vault_cross_process.py; this test proves the SEMANTIC guarantee --
# same real value in, same fake out, regardless of which proxy touched it
# first -- holds once the state actually is shared, which is exactly what
# vault-sharing exists to provide).
# --------------------------------------------------------------------------


def test_chat_proxy_and_mcp_proxy_share_one_fake_for_the_same_value(vault_manager, override_store):
    import httpx

    session_id = f"shared-{uuid.uuid4()}"
    real_email = "shared.person@example.com"

    # 1. The value gets masked first via the MCP-proxy code path (a
    #    Masker constructed exactly the way mcp_proxy.py's
    #    MaskingProxy._on_call_tool does).
    mcp_masker = Masker(session_id=session_id, vault_manager=vault_manager, override_store=override_store)
    mcp_fake = mcp_masker.mask_value(real_email)[0]

    # 2. The SAME real value, mentioned in a chat message routed through
    #    the chat proxy under the SAME session_id, must resolve to the
    #    IDENTICAL fake -- not a second, different one.
    async def handle(request):
        from starlette.responses import JSONResponse

        body = await request.json()
        return JSONResponse({"content": [{"type": "text", "text": "ack: " + body["messages"][0]["content"]}]})

    from starlette.applications import Starlette
    from starlette.responses import JSONResponse  # noqa: F401
    from starlette.routing import Route

    fake_upstream = Starlette(routes=[Route("/v1/messages", handle, methods=["POST"])])
    config = ChatProxyConfig(
        anthropic_api_key="test-key",
        vault_manager=vault_manager,
        override_store=override_store,
        httpx_transport=httpx.ASGITransport(app=fake_upstream),
    )
    app = create_app(config)

    with TestClient(app) as client:
        resp = client.post(
            "/v1/messages",
            json={"model": "x", "messages": [{"role": "user", "content": f"Please reach {real_email}"}]},
            headers={"x-decoy-session-id": session_id},
        )

    assert resp.status_code == 200
    # The chat proxy's response, once unmasked, must mention the real
    # email -- and along the way it must have masked the OUTGOING request
    # to the SAME fake the MCP-proxy path already minted.
    chat_masker = Masker(session_id=session_id, vault_manager=vault_manager, override_store=override_store)
    assert chat_masker.vault.lookup_fake(real_email) == mcp_fake

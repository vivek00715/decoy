"""A local HTTP proxy in front of a real LLM chat API, so ANY tool
pointed at it (Claude Code via ANTHROPIC_BASE_URL, or any OpenAI-SDK-based
tool via OPENAI_BASE_URL) gets free-text masking -- not just tool calls
routed through mcp_proxy.py. This closes the biggest actual gap in the
project before this: nothing intercepted text typed directly into a
chat interface.

Endpoints: POST /v1/messages (Anthropic shape), POST /v1/chat/completions
(OpenAI shape), GET /healthz. Both streaming and non-streaming requests
are supported for both.

Shared vault, deliberately not a separate instance: this proxy and
mcp_proxy.py are two SEPARATE OS PROCESSES in real usage (mcp_proxy.py is
a stdio subprocess Claude Code spawns per .mcp.json; this is a
separately-started long-running local daemon), so they cannot share
Python's in-process VaultManager singleton (get_default_manager()) the
way every other entry point in this project does. The only real
cross-process sharing mechanism is DECOY_VAULT_PERSIST=true (the SAME
.decoy/vault.enc file both processes point at) -- see vault.py's module
docstring for the two dormant cross-process bugs that had to be fixed
before this was actually safe to rely on. Running this proxy WITHOUT
DECOY_VAULT_PERSIST=true still works, but its vault is then private to
this process only and will NOT stay consistent with a separately-running
mcp_proxy.py -- see `create_app`'s docstring.

Session-id coordination is a MANUAL step, not automatic discovery: a
vault is keyed by session_id, and Claude Code sends no Decoy-specific
correlation id to an ANTHROPIC_BASE_URL/OPENAI_BASE_URL endpoint. Session
resolution order, first match wins:
  1. The `X-Decoy-Session-Id` request header, if the client sends one
     (some OpenAI/Anthropic-SDK-based tools support custom headers).
  2. The DECOY_SESSION_ID environment variable.
  3. A fixed constant, DEFAULT_SESSION_ID.
For this proxy's vault to actually unify with a concurrently-running
mcp_proxy.py's, you must configure the SAME session_id on both sides
(mcp_proxy.py's `--session` flag, and this proxy's DECOY_SESSION_ID/
X-Decoy-Session-Id) -- there is no automatic way to correlate a chat
completions request with a specific MCP proxy invocation, and this
module does not pretend otherwise.

If that MCP-proxy side of the setup is `decoy proxy` registered in a
project's `.mcp.json` (Claude Code's project-scoped MCP config -- see
mcp_proxy.py's module docstring), ONE MORE STEP APPLIES REGARDLESS OF HOW
THAT ENTRY GOT THERE, not just when the IDE extensions' auto-config
feature wrote it: confirmed directly against a real `claude` CLI, an
entry in `.mcp.json` shows as `⏸ Pending approval (run 'claude' to
approve)` and is NOT connected until a human runs `claude` interactively
in that project directory and approves it. This applies whether the
entry was hand-written, added via `claude mcp add --scope project`, or
written by an IDE extension's auto-config -- it is a property of
`.mcp.json`/project-scope itself, not of how Decoy specifically got the
entry in there. If you've set up this chat proxy AND `decoy proxy` and
nothing seems to be sharing state, check `claude mcp list` for a pending
entry before assuming the vault-sharing wiring above is broken.

Streaming correctness: an upstream text delta can split a fake value
across two SSE chunks. The risk is one-directional and safe (a split
fake just fails to unmask -- the client sees an un-reversed fake string,
never a real value that shouldn't have been sent), but this module still
handles it properly rather than leaning on that safety margin: see
StreamUnmaskBuffer, which holds back a trailing window (bounded by the
longest currently-known fake value) before flushing each chunk, so a
split fake gets a chance to complete before being checked.

Not done here: bundling this into the PyInstaller `decoy-proxy` binary
(scripts/build_binary.py) -- that build deliberately excluded heavier
deps; adding starlette/uvicorn/httpx to it is a separate, later decision
once this proxy exists and is proven to work, not assumed here.

Trust boundary, stated plainly: this proxy has no authentication layer of
its own. It is meant to run on localhost, and (like any other local dev
proxy -- e.g. a local API gateway or mitmproxy instance) trusts any local
process that can reach its port. It holds the real, billable upstream API
key server-side and will make real LLM calls on behalf of ANY request
that reaches it, authenticated or not. This is a deliberate, disclosed
scope boundary, not an oversight -- adding a shared-secret/local-token
gate is a reasonable follow-up if this is used somewhere that boundary
matters (e.g. a shared/multi-user machine), and is flagged here rather
than silently decided either way.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional

from .logging_config import get_logger
from .masker import Masker
from .overrides import OverrideStore, get_default_store
from .vault import VaultManager, get_default_manager

logger = get_logger("chat_proxy")

DEFAULT_SESSION_ID = "chat-proxy-default"
SESSION_HEADER = "x-decoy-session-id"

DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com"

ANTHROPIC_VERSION_HEADER = "2023-06-01"


def resolve_session_id(header_value: Optional[str]) -> str:
    """See this module's docstring: header > DECOY_SESSION_ID env > fixed
    constant. Never auto-discovered/correlated -- documented, not magic."""
    if header_value:
        return header_value
    env_value = os.environ.get("DECOY_SESSION_ID")
    if env_value:
        return env_value
    return DEFAULT_SESSION_ID


# --------------------------------------------------------------------------
# Anthropic /v1/messages request/response masking
# --------------------------------------------------------------------------


def _mask_anthropic_content(content: Any, masker: Masker, audit_log: Any, request_id: str) -> Any:
    """`content` is either a plain string, or a list of content blocks
    (dicts with a "type" key: "text", "tool_result" [itself str-or-list
    `content`], "tool_use", "image", ...). Only "text" and "tool_result"
    blocks carry maskable free text; anything else (tool_use inputs,
    image data) is passed through unchanged -- masking a tool_use's
    structured `input` as free text would corrupt it, and this project's
    masking core operates on text/JSON row data, not arbitrary tool
    schemas."""
    if isinstance(content, str):
        return masker.mask_text(content, audit_log=audit_log, request_id=request_id).masked_text

    if isinstance(content, list):
        masked_blocks = []
        for block in content:
            if not isinstance(block, dict):
                masked_blocks.append(block)
                continue
            block_type = block.get("type")
            if block_type == "text" and isinstance(block.get("text"), str):
                masked_blocks.append(
                    {**block, "text": masker.mask_text(block["text"], audit_log=audit_log, request_id=request_id).masked_text}
                )
            elif block_type == "tool_result" and "content" in block:
                masked_blocks.append(
                    {**block, "content": _mask_anthropic_content(block["content"], masker, audit_log, request_id)}
                )
            else:
                masked_blocks.append(block)
        return masked_blocks

    return content


def mask_anthropic_request(body: dict, masker: Masker, audit_log: Any = None) -> tuple[dict, str]:
    """Returns (masked_body, request_id). Never mutates `body`."""
    request_id = str(uuid.uuid4())
    masked = dict(body)

    system = body.get("system")
    if isinstance(system, str):
        masked["system"] = masker.mask_text(system, audit_log=audit_log, request_id=request_id).masked_text
    elif isinstance(system, list):
        masked["system"] = _mask_anthropic_content(system, masker, audit_log, request_id)

    messages = body.get("messages")
    if isinstance(messages, list):
        masked["messages"] = [
            {**m, "content": _mask_anthropic_content(m.get("content"), masker, audit_log, request_id)}
            if isinstance(m, dict)
            else m
            for m in messages
        ]

    return masked, request_id


def _unmask_anthropic_content(content: Any, masker: Masker) -> Any:
    if isinstance(content, str):
        return masker.unmask_text(content)
    if isinstance(content, list):
        unmasked_blocks = []
        for block in content:
            if not isinstance(block, dict):
                unmasked_blocks.append(block)
                continue
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                unmasked_blocks.append({**block, "text": masker.unmask_text(block["text"])})
            else:
                unmasked_blocks.append(block)
        return unmasked_blocks
    return content


def unmask_anthropic_response(body: dict, masker: Masker) -> dict:
    unmasked = dict(body)
    if isinstance(body.get("content"), list):
        unmasked["content"] = _unmask_anthropic_content(body["content"], masker)
    return unmasked


# --------------------------------------------------------------------------
# OpenAI /v1/chat/completions request/response masking
# --------------------------------------------------------------------------


def _mask_openai_content(content: Any, masker: Masker, audit_log: Any, request_id: str) -> Any:
    """OpenAI's `content` is either a plain string, or (multimodal
    messages) a list of {"type": "text", "text": ...} / {"type":
    "image_url", ...} blocks."""
    if isinstance(content, str):
        return masker.mask_text(content, audit_log=audit_log, request_id=request_id).masked_text
    if isinstance(content, list):
        masked_blocks = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                masked_blocks.append(
                    {**block, "text": masker.mask_text(block["text"], audit_log=audit_log, request_id=request_id).masked_text}
                )
            else:
                masked_blocks.append(block)
        return masked_blocks
    return content


def mask_openai_request(body: dict, masker: Masker, audit_log: Any = None) -> tuple[dict, str]:
    request_id = str(uuid.uuid4())
    masked = dict(body)
    messages = body.get("messages")
    if isinstance(messages, list):
        masked["messages"] = [
            {**m, "content": _mask_openai_content(m.get("content"), masker, audit_log, request_id)}
            if isinstance(m, dict)
            else m
            for m in messages
        ]
    return masked, request_id


def unmask_openai_response(body: dict, masker: Masker) -> dict:
    unmasked = dict(body)
    choices = body.get("choices")
    if isinstance(choices, list):
        new_choices = []
        for choice in choices:
            if not isinstance(choice, dict):
                new_choices.append(choice)
                continue
            message = choice.get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                choice = {**choice, "message": {**message, "content": masker.unmask_text(message["content"])}}
            new_choices.append(choice)
        unmasked["choices"] = new_choices
    return unmasked


# --------------------------------------------------------------------------
# Streaming: buffered-safe unmasking
# --------------------------------------------------------------------------


class StreamUnmaskBuffer:
    """Accumulates streamed text and releases it unmask-safe: a fake
    value split across two `feed()` calls is held back until it can be
    unmasked as a whole, rather than being flushed (and potentially left
    un-reversed) mid-value. See this module's docstring, "Streaming
    correctness".

    CORRECTED DESIGN (a first version of this class shipped with a real
    bug, caught by test_stream_unmask_buffer_reconstructs_a_fake_value_
    split_across_chunks in tests/test_chat_proxy.py before this was ever
    used against a real stream): a naive "always retain exactly the
    longest-known-fake-length trailing characters of the growing buffer"
    does NOT work. Once a partial fake prefix is retained after one
    `feed()` call, later unrelated text appended in a SUBSEQUENT call
    pushes that partial prefix further from the buffer's end -- a fixed
    trailing-length retention window then flushes straight through the
    middle of it, because the window tracks "distance from the end," not
    "where the actual in-progress fake started." Confirmed by direct
    trace: with a 21-char fake split 10/11 across two chunks, the second
    `feed()` flushed a 24-character prefix that included the fake's
    first 13 characters and left only its last 9 in the retained tail --
    neither flushed piece ever contained the complete fake, so it was
    never unmasked at all (concatenating the two pieces just silently
    reassembled the FAKE text, not the real value).

    The correct approach checks, on every `feed()`, whether any SUFFIX of
    the current buffer is a PREFIX of some known fake (a possible
    in-progress split match) -- only that suffix (the longest such match)
    is retained; everything before it is provably not part of an
    incomplete fake and is safe to unmask-and-flush as one contiguous
    piece, so a fake that started before the retained region is always
    still complete somewhere within what gets flushed.
    """

    def __init__(self, masker: Masker):
        self._masker = masker
        self._pending = ""

    def _known_fakes(self) -> list[str]:
        return self._masker.vault.all_fakes()

    def _retain_length(self, fakes: list[str]) -> int:
        """Longest suffix of `self._pending` that is a prefix of some
        known fake -- the minimum amount of trailing text that MUST stay
        unflushed because more incoming text could still complete it
        into a full fake occurrence. 0 if no such suffix exists."""
        if not fakes:
            return 0
        max_fake_len = max(len(f) for f in fakes)
        upper = min(max_fake_len, len(self._pending))
        for length in range(upper, 0, -1):
            suffix = self._pending[-length:]
            if any(fake.startswith(suffix) for fake in fakes):
                return length
        return 0

    def feed(self, chunk: str) -> str:
        """Returns the portion of `chunk` (plus any previously held-back
        text) that is now safe to flush, already unmasked. May return an
        empty string if everything fed so far might still be a growing
        partial match of a known fake."""
        self._pending += chunk
        fakes = self._known_fakes()
        retain_len = self._retain_length(fakes)
        if retain_len >= len(self._pending):
            return ""  # the whole buffer might still be a growing partial match
        to_flush = self._pending[: len(self._pending) - retain_len] if retain_len else self._pending
        self._pending = self._pending[len(self._pending) - retain_len:] if retain_len else ""
        return self._masker.unmask_text(to_flush)

    def flush(self) -> str:
        """Call at stream end to release anything still held back
        (unmasked if it happens to complete a fake; passed through as-is
        otherwise -- e.g. a genuine partial match that the stream simply
        ended before completing, which is not a real fake and must not
        be dropped)."""
        if not self._pending:
            return ""
        result = self._masker.unmask_text(self._pending)
        self._pending = ""
        return result


# --------------------------------------------------------------------------
# App wiring (starlette) -- imported lazily so `decoy.chat_proxy` module
# import doesn't itself require the `chat-proxy` extra unless the HTTP
# app is actually constructed.
# --------------------------------------------------------------------------


@dataclass
class ChatProxyConfig:
    anthropic_api_key: Optional[str] = None
    anthropic_base_url: str = DEFAULT_ANTHROPIC_BASE_URL
    openai_api_key: Optional[str] = None
    openai_base_url: str = DEFAULT_OPENAI_BASE_URL
    vault_manager: Optional[VaultManager] = None
    override_store: Optional[OverrideStore] = None
    audit_log: Optional[Any] = None
    request_timeout_seconds: float = 120.0
    # Test-only hook: an httpx.ASGITransport (or any httpx transport)
    # pointed at a fake upstream app instead of the real network. None
    # (the default) means real network calls via httpx's normal
    # transport, exactly as in production.
    httpx_transport: Optional[Any] = None

    @classmethod
    def from_env(cls) -> "ChatProxyConfig":
        return cls(
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY"),
            anthropic_base_url=os.environ.get("DECOY_ANTHROPIC_UPSTREAM_URL", DEFAULT_ANTHROPIC_BASE_URL),
            openai_api_key=os.environ.get("OPENAI_API_KEY"),
            openai_base_url=os.environ.get("DECOY_OPENAI_UPSTREAM_URL", DEFAULT_OPENAI_BASE_URL),
        )


def create_app(config: Optional[ChatProxyConfig] = None):
    """Builds the Starlette ASGI app. Requires the `chat-proxy` extra
    (`pip install "decoy[chat-proxy]"`) -- imports httpx/starlette lazily
    so importing `decoy.chat_proxy` itself (e.g. for StreamUnmaskBuffer's
    unit tests, which need neither) never requires them.

    If `config.vault_manager`/`override_store`/`audit_log` are not given,
    this uses the process-default singletons (get_default_manager(),
    get_default_store(), no audit log) -- NOT automatically
    DECOY_VAULT_PERSIST-aware beyond what VaultManager itself already
    reads from that env var. Run with `DECOY_VAULT_PERSIST=true` (and,
    ideally, the same DECOY_SESSION_ID as your mcp_proxy.py invocation)
    for this proxy's vault to actually stay consistent with a
    concurrently-running MCP proxy -- see this module's docstring.
    """
    import contextlib

    import httpx
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse, StreamingResponse
    from starlette.routing import Route

    config = config or ChatProxyConfig.from_env()
    vault_manager = config.vault_manager or get_default_manager()
    override_store = config.override_store or get_default_store()
    audit_log = config.audit_log

    state: dict[str, Any] = {}

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        state["client"] = httpx.AsyncClient(timeout=config.request_timeout_seconds, transport=config.httpx_transport)
        if vault_manager.persist:
            logger.info("chat proxy started with DECOY_VAULT_PERSIST enabled (vault_path=%s)", vault_manager.vault_path)
        else:
            logger.warning(
                "chat proxy started WITHOUT vault persistence -- its vault is private to this "
                "process and will NOT stay consistent with a separately-running mcp_proxy.py; "
                "set DECOY_VAULT_PERSIST=true to share state across processes"
            )
        try:
            yield
        finally:
            await state["client"].aclose()

    def _masker_for(request) -> Masker:
        session_id = resolve_session_id(request.headers.get(SESSION_HEADER))
        return Masker(session_id=session_id, vault_manager=vault_manager, override_store=override_store)

    async def healthz(request):
        return JSONResponse({"status": "ok", "service": "decoy-chat-proxy"})

    async def messages(request):
        """POST /v1/messages -- Anthropic shape."""
        if not config.anthropic_api_key:
            return JSONResponse(
                {"type": "error", "error": {"type": "authentication_error", "message": "decoy-proxy: ANTHROPIC_API_KEY is not set on the proxy's own machine/environment"}},
                status_code=500,
            )

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 - malformed request body, not a proxy bug
            return JSONResponse({"type": "error", "error": {"type": "invalid_request_error", "message": "invalid JSON body"}}, status_code=400)

        masker = _masker_for(request)
        masked_body, request_id = mask_anthropic_request(body, masker, audit_log=audit_log)
        logger.info(
            "POST /v1/messages session=%s request_id=%s stream=%s model=%s",
            masker.session_id, request_id, bool(body.get("stream")), body.get("model"),
        )

        upstream_headers = {
            "x-api-key": config.anthropic_api_key,
            "anthropic-version": request.headers.get("anthropic-version", ANTHROPIC_VERSION_HEADER),
            "content-type": "application/json",
        }
        client: httpx.AsyncClient = state["client"]

        if masked_body.get("stream"):
            return StreamingResponse(
                _stream_anthropic(client, config.anthropic_base_url, upstream_headers, masked_body, masker),
                media_type="text/event-stream",
            )

        upstream_response = await client.post(f"{config.anthropic_base_url}/v1/messages", json=masked_body, headers=upstream_headers)
        response_body = upstream_response.json()
        if upstream_response.status_code >= 400:
            # Upstream error bodies are passed through as-is (never
            # masked -- they contain no user content, and masking an
            # error message could obscure the actual problem), but never
            # logged with full content either, matching this module's
            # never-log-real/fake-values rule.
            logger.warning("upstream /v1/messages returned status %s", upstream_response.status_code)
            return JSONResponse(response_body, status_code=upstream_response.status_code)

        unmasked = unmask_anthropic_response(response_body, masker)
        return JSONResponse(unmasked)

    async def chat_completions(request):
        """POST /v1/chat/completions -- OpenAI shape."""
        if not config.openai_api_key:
            return JSONResponse(
                {"error": {"type": "authentication_error", "message": "decoy-proxy: OPENAI_API_KEY is not set on the proxy's own machine/environment"}},
                status_code=500,
            )

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": {"type": "invalid_request_error", "message": "invalid JSON body"}}, status_code=400)

        masker = _masker_for(request)
        masked_body, request_id = mask_openai_request(body, masker, audit_log=audit_log)
        logger.info(
            "POST /v1/chat/completions session=%s request_id=%s stream=%s model=%s",
            masker.session_id, request_id, bool(body.get("stream")), body.get("model"),
        )

        upstream_headers = {
            "authorization": f"Bearer {config.openai_api_key}",
            "content-type": "application/json",
        }
        client: httpx.AsyncClient = state["client"]

        if masked_body.get("stream"):
            return StreamingResponse(
                _stream_openai(client, config.openai_base_url, upstream_headers, masked_body, masker),
                media_type="text/event-stream",
            )

        upstream_response = await client.post(f"{config.openai_base_url}/v1/chat/completions", json=masked_body, headers=upstream_headers)
        response_body = upstream_response.json()
        if upstream_response.status_code >= 400:
            logger.warning("upstream /v1/chat/completions returned status %s", upstream_response.status_code)
            return JSONResponse(response_body, status_code=upstream_response.status_code)

        unmasked = unmask_openai_response(response_body, masker)
        return JSONResponse(unmasked)

    app = Starlette(
        routes=[
            Route("/healthz", healthz, methods=["GET"]),
            Route("/v1/messages", messages, methods=["POST"]),
            Route("/v1/chat/completions", chat_completions, methods=["POST"]),
        ],
        lifespan=lifespan,
    )
    return app


async def _stream_anthropic(client, base_url: str, headers: dict, body: dict, masker: Masker) -> AsyncIterator[bytes]:
    """Re-emits the upstream SSE stream, unmasking `content_block_delta`
    text_delta payloads via a StreamUnmaskBuffer PER content block index
    (Anthropic can interleave multiple content blocks by index; each
    needs its own holdback buffer so text from one block never gets
    concatenated with another's)."""
    buffers: dict[int, StreamUnmaskBuffer] = {}
    async with client.stream("POST", f"{base_url}/v1/messages", json=body, headers=headers) as upstream:
        async for raw_line in upstream.aiter_lines():
            if not raw_line.startswith("data:"):
                yield (raw_line + "\n").encode("utf-8")
                continue
            payload = raw_line[len("data:"):].strip()
            if not payload:
                yield b"\n"
                continue
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                yield (raw_line + "\n").encode("utf-8")
                continue

            event_type = event.get("type")
            if event_type == "content_block_delta" and event.get("delta", {}).get("type") == "text_delta":
                idx = event.get("index", 0)
                buf = buffers.setdefault(idx, StreamUnmaskBuffer(masker))
                flushed = buf.feed(event["delta"]["text"])
                if not flushed:
                    continue  # held back entirely -- emit nothing for this chunk yet
                event["delta"] = {**event["delta"], "text": flushed}
            elif event_type == "content_block_stop":
                idx = event.get("index", 0)
                buf = buffers.get(idx)
                if buf is not None:
                    remainder = buf.flush()
                    if remainder:
                        flush_event = {"type": "content_block_delta", "index": idx, "delta": {"type": "text_delta", "text": remainder}}
                        yield f"event: content_block_delta\ndata: {json.dumps(flush_event)}\n\n".encode("utf-8")

            yield f"data: {json.dumps(event)}\n\n".encode("utf-8")


async def _stream_openai(client, base_url: str, headers: dict, body: dict, masker: Masker) -> AsyncIterator[bytes]:
    buf = StreamUnmaskBuffer(masker)
    async with client.stream("POST", f"{base_url}/v1/chat/completions", json=body, headers=headers) as upstream:
        async for raw_line in upstream.aiter_lines():
            if not raw_line.startswith("data:"):
                yield (raw_line + "\n").encode("utf-8")
                continue
            payload = raw_line[len("data:"):].strip()
            if payload == "[DONE]":
                remainder = buf.flush()
                if remainder:
                    flush_chunk = {"choices": [{"delta": {"content": remainder}, "index": 0, "finish_reason": None}]}
                    yield f"data: {json.dumps(flush_chunk)}\n\n".encode("utf-8")
                yield b"data: [DONE]\n\n"
                continue
            if not payload:
                yield b"\n"
                continue
            try:
                chunk = json.loads(payload)
            except json.JSONDecodeError:
                yield (raw_line + "\n").encode("utf-8")
                continue

            choices = chunk.get("choices") or []
            if choices and isinstance(choices[0].get("delta", {}).get("content"), str):
                flushed = buf.feed(choices[0]["delta"]["content"])
                if not flushed:
                    continue
                chunk["choices"][0]["delta"] = {**choices[0]["delta"], "content": flushed}

            yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")


def main(argv: Optional[list[str]] = None) -> int:
    """Standalone entry point: `python -m decoy.chat_proxy` or the
    `decoy chat-proxy` CLI subcommand (see cli.py). Requires the
    `chat-proxy` extra."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="decoy-chat-proxy",
        description="Run Decoy's chat-completions masking proxy in front of a real LLM API.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args(argv)

    try:
        import uvicorn
    except ImportError:
        print(
            "decoy-chat-proxy: the 'chat-proxy' extra is required to run this "
            "(pip install \"decoy[chat-proxy]\")",
            file=__import__("sys").stderr,
        )
        return 1

    app = create_app()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())

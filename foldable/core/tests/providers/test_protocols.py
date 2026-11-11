"""Provider contract tests (T4): deterministic local test servers for every wire
protocol. These are MOCK tests – they prove parsing, error mapping, parameter
allowlists and cancellation, not that a real provider answers."""

from __future__ import annotations

import asyncio
import json

import pytest

from spaider_foldable.providers.anthropic import AnthropicMessagesAdapter
from spaider_foldable.providers.base import AdapterConfig
from spaider_foldable.providers.chat_completions import ChatCompletionsAdapter
from spaider_foldable.providers.events import GenerationRequest
from spaider_foldable.providers.responses import ResponsesAdapter

from .fakeserver import FakeProvider, Scripted, cc_chunk, split_bytes, sse

pytestmark = pytest.mark.asyncio


@pytest.fixture
def fake():
    f = FakeProvider().start()
    yield f
    f.stop()


def cfg(fake: FakeProvider, **kw) -> AdapterConfig:
    base = dict(connection_id="c1", provider="test", base_url=fake.base_url + "/v1", api_key="sk-test", execution="lan", allow_insecure_http=True)
    base.update(kw)
    return AdapterConfig(**base)


async def collect(adapter, req):
    events = []
    async for ev in adapter.generate(req):
        events.append(ev)
    await adapter.aclose()
    return events


def kinds(events):
    return [e.kind for e in events]


REQ = GenerationRequest(messages=[{"role": "system", "content": "Du bist knapp."}, {"role": "user", "content": "Hallo"}], model="m1", max_tokens=32, temperature=0.2, timeout_s=10.0)


# ---- chat completions --------------------------------------------------------------------
async def test_cc_normal_text_with_usage_and_fragmented_utf8(fake):
    body = sse(cc_chunk("Grü"), cc_chunk("ße, Ü"), cc_chunk("ber"), cc_chunk(finish="stop", usage={"prompt_tokens": 7, "completion_tokens": 3}))
    fake.on("POST", "/v1/chat/completions", Scripted(chunks=split_bytes(body, 7)))  # splits inside multi-byte chars
    ev = await collect(ChatCompletionsAdapter(cfg(fake)), REQ)
    assert "".join(e.text for e in ev if e.kind == "text_delta") == "Grüße, Über"
    assert kinds(ev)[0] == "start" and kinds(ev)[-1] == "completed"
    assert ev[-1].finish_reason == "stop" and ev[-1].usage["prompt_tokens"] == 7
    sent = fake.requests[-1]
    assert sent.headers["authorization"] == "Bearer sk-test"
    assert sent.body["stream"] is True and sent.body["temperature"] == 0.2 and sent.body["max_tokens"] == 32
    assert sent.body["messages"][0]["role"] == "system"


async def test_cc_param_allowlist_strips_unsupported_fields(fake):
    fake.on("POST", "/v1/chat/completions", Scripted(chunks=[sse(cc_chunk("ok", finish="stop"))]))
    adapter = ChatCompletionsAdapter(cfg(fake, param_allowlist={"stream", "max_tokens"}))
    await collect(adapter, REQ)
    body = fake.requests[-1].body
    assert "temperature" not in body and "stream_options" not in body and body["max_tokens"] == 32


@pytest.mark.parametrize("status,code", [(401, "unauthorized"), (403, "forbidden"), (429, "rate_limited"), (500, "server_error"), (503, "server_error"), (404, "not_found")])
async def test_cc_http_errors_are_mapped(fake, status, code):
    fake.on("POST", "/v1/chat/completions", Scripted(status=status, content_type="application/json", body=b'{"error":{"message":"nope"}}', headers={"Retry-After": "7"} if status == 429 else {}))
    ev = await collect(ChatCompletionsAdapter(cfg(fake)), REQ)
    assert kinds(ev) == ["error"]
    assert ev[0].error.code == code and ev[0].error.status == status
    if status == 429:
        assert ev[0].error.retry_after_s == 7.0 and ev[0].error.retryable


async def test_cc_json_instead_of_sse_is_accepted(fake):
    fake.on("POST", "/v1/chat/completions", Scripted(content_type="application/json", body=json.dumps({"choices": [{"message": {"role": "assistant", "content": "Antwort"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 3, "completion_tokens": 1}}).encode()))
    ev = await collect(ChatCompletionsAdapter(cfg(fake)), REQ)
    assert [e.kind for e in ev] == ["start", "text_delta", "usage", "completed"]
    assert ev[1].text == "Antwort"


async def test_cc_tool_only_turn_yields_tool_call_and_completed(fake):
    body = sse(
        cc_chunk(tool={"index": 0, "id": "call_1", "type": "function", "function": {"name": "files.read", "arguments": ""}}),
        cc_chunk(tool={"index": 0, "function": {"arguments": '{"path": "a.txt"}'}}),
        cc_chunk(finish="tool_calls"),
    )
    fake.on("POST", "/v1/chat/completions", Scripted(chunks=[body]))
    ev = await collect(ChatCompletionsAdapter(cfg(fake)), GenerationRequest(messages=REQ.messages, model="m1", tools=[{"type": "function", "function": {"name": "files.read", "parameters": {}}}], timeout_s=10))
    tc = [e for e in ev if e.kind == "tool_call"]
    assert len(tc) == 1 and tc[0].tool_call.name == "files.read" and json.loads(tc[0].tool_call.arguments)["path"] == "a.txt"
    assert ev[-1].kind == "completed" and ev[-1].finish_reason == "tool_calls"


async def test_cc_reasoning_only_end_is_an_empty_answer_error(fake):
    fake.on("POST", "/v1/chat/completions", Scripted(chunks=[sse(cc_chunk(reasoning="Ich denke nach"), cc_chunk(finish="stop"))]))
    ev = await collect(ChatCompletionsAdapter(cfg(fake)), REQ)
    assert any(e.kind == "reasoning_delta" for e in ev)
    assert ev[-1].kind == "error" and ev[-1].error.code == "empty_answer"


async def test_cc_stream_error_object_like_llama_oom(fake):
    body = sse(cc_chunk("Teil"), {"error": {"code": 500, "message": "decode() failed: vk::Device::allocateMemory: ErrorOutOfDeviceMemory", "type": "server_error"}}, done=False)
    fake.on("POST", "/v1/chat/completions", Scripted(chunks=[body]))
    ev = await collect(ChatCompletionsAdapter(cfg(fake)), REQ)
    assert ev[1].kind == "text_delta" and ev[-1].kind == "error"
    assert ev[-1].error.code == "out_of_memory"


async def test_cc_interrupted_connection_is_a_network_error_not_done(fake):
    body = sse(cc_chunk("Anfang"), cc_chunk(" weiter"), cc_chunk(finish="stop"))
    fake.on("POST", "/v1/chat/completions", Scripted(chunks=split_bytes(body, 20), abort_after=2))
    ev = await collect(ChatCompletionsAdapter(cfg(fake)), REQ)
    assert "completed" not in kinds(ev)
    assert ev[-1].kind == "error" and ev[-1].error.code in ("network", "empty_answer")


async def test_cc_unknown_fields_and_heartbeats_are_tolerated(fake):
    body = b": keep-alive\n\n" + sse({"id": "x", "object": "weird.event", "foo": 1}, cc_chunk("ok"), "event: ping\ndata: {}\n\n", cc_chunk(finish="stop"))
    fake.on("POST", "/v1/chat/completions", Scripted(chunks=[body]))
    ev = await collect(ChatCompletionsAdapter(cfg(fake)), REQ)
    assert "".join(e.text for e in ev if e.kind == "text_delta") == "ok" and ev[-1].kind == "completed"


async def test_cc_cancel_closes_stream(fake):
    body = sse(*[cc_chunk("x") for _ in range(200)], cc_chunk(finish="stop"))
    fake.on("POST", "/v1/chat/completions", Scripted(chunks=split_bytes(body, 40), delay_s=0.0))
    adapter = ChatCompletionsAdapter(cfg(fake))
    got = []

    async def run():
        async for e in adapter.generate(REQ):
            got.append(e.kind)
            if len(got) == 5:
                await adapter.cancel()

    await asyncio.wait_for(run(), timeout=10)
    assert "completed" not in got and got[-1] == "error"  # closed by us -> reported as broken stream, never as done
    await adapter.aclose()


async def test_cc_models_list(fake):
    fake.on("GET", "/v1/models", Scripted(content_type="application/json", body=json.dumps({"data": [{"id": "m1", "owned_by": "t", "context_length": 4096}, {"id": "m2"}]}).encode()))
    models = await ChatCompletionsAdapter(cfg(fake)).list_models()
    assert [m.id for m in models] == ["m1", "m2"] and models[0].context_length == 4096


async def test_insecure_http_requires_explicit_private_opt_in():
    with pytest.raises(Exception):
        ChatCompletionsAdapter(AdapterConfig(connection_id="c", provider="t", base_url="http://example.com/v1", allow_insecure_http=True))


async def test_cc_images_rejected_when_preset_has_no_vision(fake):
    adapter = ChatCompletionsAdapter(cfg(fake, extra={"vision": False}))
    req = GenerationRequest(messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]}], model="m1")
    ev = await collect(adapter, req)
    assert kinds(ev) == ["error"] and ev[0].error.code == "unsupported"


# ---- responses -------------------------------------------------------------------------------
def resp_events(*objs, extra_terminal=True):
    items = [dict(o) for o in objs]
    out = []
    for o in items:
        out.append(f"event: {o['type']}\ndata: {json.dumps(o)}\n\n")
    return "".join(out).encode()


async def test_responses_text_stream_and_completed(fake):
    body = resp_events({"type": "response.created", "response": {"id": "r1"}}, {"type": "response.output_text.delta", "delta": "Hal"}, {"type": "response.output_text.delta", "delta": "lo"}, {"type": "response.completed", "response": {"usage": {"input_tokens": 5, "output_tokens": 2}}})
    fake.on("POST", "/v1/responses", Scripted(chunks=split_bytes(body, 13)))
    adapter = ResponsesAdapter(cfg(fake))
    ev = await collect(adapter, REQ)
    assert "".join(e.text for e in ev if e.kind == "text_delta") == "Hallo" and ev[-1].kind == "completed"
    assert ev[-1].usage["prompt_tokens"] == 5
    body_sent = fake.requests[-1].body
    assert body_sent["instructions"] == "Du bist knapp." and body_sent["input"][0]["role"] == "user" and body_sent["stream"] is True


async def test_responses_siwc_allowlist_forces_store_false_and_no_temperature(fake):
    body = resp_events({"type": "response.output_text.delta", "delta": "x"}, {"type": "response.completed", "response": {}})
    fake.on("POST", "/v1/responses", Scripted(chunks=[body]))
    adapter = ResponsesAdapter(cfg(fake, param_allowlist={"tools", "text", "reasoning", "instructions"}, extra={"store_false": True, "request_defaults": {"store": False}}))
    await collect(adapter, REQ)
    sent = fake.requests[-1].body
    assert sent["store"] is False and "temperature" not in sent and "max_output_tokens" not in sent


async def test_responses_failed_and_incomplete(fake):
    fake.on("POST", "/v1/responses", Scripted(chunks=[resp_events({"type": "response.failed", "response": {"error": {"code": "subscription_sharing_usage_limit_exceeded", "message": "limit"}}})]))
    ev = await collect(ResponsesAdapter(cfg(fake)), REQ)
    assert ev[-1].kind == "error" and ev[-1].error.code == "rate_limited" and ev[-1].error.status == 429
    fake.on("POST", "/v1/responses", Scripted(chunks=[resp_events({"type": "response.output_text.delta", "delta": "halb"}, {"type": "response.incomplete", "response": {"incomplete_details": {"reason": "max_output_tokens"}, "usage": {"input_tokens": 1, "output_tokens": 1}}})]))
    ev = await collect(ResponsesAdapter(cfg(fake)), REQ)
    assert ev[-1].kind == "completed" and ev[-1].finish_reason == "length"


async def test_responses_stream_without_completed_is_an_error(fake):
    fake.on("POST", "/v1/responses", Scripted(chunks=[resp_events({"type": "response.output_text.delta", "delta": "x"})]))
    ev = await collect(ResponsesAdapter(cfg(fake)), REQ)
    assert ev[-1].kind == "error" and ev[-1].error.code == "network"


async def test_responses_function_call(fake):
    body = resp_events(
        {"type": "response.output_item.added", "item": {"type": "function_call", "id": "fc1", "call_id": "call_9", "name": "files.read", "arguments": ""}},
        {"type": "response.function_call_arguments.delta", "item_id": "fc1", "delta": '{"path":'},
        {"type": "response.function_call_arguments.delta", "item_id": "fc1", "delta": ' "a.txt"}'},
        {"type": "response.output_item.done", "item": {"type": "function_call", "id": "fc1", "call_id": "call_9", "name": "files.read", "arguments": '{"path": "a.txt"}'}},
        {"type": "response.completed", "response": {}},
    )
    fake.on("POST", "/v1/responses", Scripted(chunks=[body]))
    ev = await collect(ResponsesAdapter(cfg(fake)), REQ)
    tc = [e for e in ev if e.kind == "tool_call"]
    assert tc and tc[0].tool_call.id == "call_9" and json.loads(tc[0].tool_call.arguments)["path"] == "a.txt" and ev[-1].kind == "completed"


async def test_responses_models_siwc_visibility_filter(fake):
    fake.on("GET", "/v1/models", Scripted(content_type="application/json", body=json.dumps({"models": [{"slug": "gpt-a", "display_name": "A", "visibility": "list"}, {"slug": "gpt-hidden", "visibility": "hidden"}]}).encode()))
    models = await ResponsesAdapter(cfg(fake)).list_models()
    assert [m.id for m in models] == ["gpt-a"]


# ---- anthropic messages ------------------------------------------------------------------------
def ant_events(*objs):
    return "".join(f"event: {o['type']}\ndata: {json.dumps(o)}\n\n" for o in objs).encode()


async def test_anthropic_text_tool_and_stop(fake):
    body = ant_events(
        {"type": "message_start", "message": {"usage": {"input_tokens": 9}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hi "}},
        {"type": "ping"},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "da"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "toolu_1", "name": "files.read", "input": {}}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": '{"path": '}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": '"a.txt"}'}},
        {"type": "content_block_stop", "index": 1},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 12}},
        {"type": "message_stop"},
    )
    fake.on("POST", "/v1/messages", Scripted(chunks=split_bytes(body, 31)))
    adapter = AnthropicMessagesAdapter(cfg(fake, extra={"anthropic_version": "2026-10-21"}))
    ev = await collect(adapter, REQ)
    assert "".join(e.text for e in ev if e.kind == "text_delta") == "Hi da"
    tc = [e for e in ev if e.kind == "tool_call"][0].tool_call
    assert tc.id == "toolu_1" and json.loads(tc.arguments)["path"] == "a.txt"
    assert ev[-1].kind == "completed" and ev[-1].finish_reason == "tool_calls" and ev[-1].usage["prompt_tokens"] == 9
    sent = fake.requests[-1]
    assert sent.headers["x-api-key"] == "sk-test" and sent.headers["anthropic-version"] == "2026-10-21" and "authorization" not in sent.headers
    assert sent.body["system"] == "Du bist knapp." and sent.body["messages"][0]["role"] == "user" and sent.body["max_tokens"] == 32


async def test_anthropic_error_event_and_missing_stop(fake):
    fake.on("POST", "/v1/messages", Scripted(chunks=[ant_events({"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}})]))
    ev = await collect(AnthropicMessagesAdapter(cfg(fake)), REQ)
    assert ev[-1].kind == "error" and ev[-1].error.code == "rate_limited"
    fake.on("POST", "/v1/messages", Scripted(chunks=[ant_events({"type": "message_start", "message": {}}, {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "x"}})]))
    ev = await collect(AnthropicMessagesAdapter(cfg(fake)), REQ)
    assert ev[-1].kind == "error" and ev[-1].error.code == "network"


async def test_anthropic_thinking_delta_is_reasoning_and_empty_answer(fake):
    fake.on("POST", "/v1/messages", Scripted(chunks=[ant_events({"type": "content_block_start", "index": 0, "content_block": {"type": "thinking"}}, {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "hmm"}}, {"type": "content_block_stop", "index": 0}, {"type": "message_delta", "delta": {"stop_reason": "end_turn"}}, {"type": "message_stop"})]))
    ev = await collect(AnthropicMessagesAdapter(cfg(fake)), REQ)
    assert any(e.kind == "reasoning_delta" and e.reasoning == "hmm" for e in ev)
    assert ev[-1].kind == "error" and ev[-1].error.code == "empty_answer"


# ---- access check: four separate stages ------------------------------------------------
async def test_access_check_stages_and_probe_fallback(fake):
    fake.on("GET", "/v1/models", Scripted(200, "application/json", json.dumps({"data": [{"id": "m1"}, {"id": "m2"}]}).encode()))
    fake.on("POST", "/v1/chat/completions", Scripted(chunks=[sse(cc_chunk("OK"), cc_chunk(finish="stop"))]))
    a = ChatCompletionsAdapter(cfg(fake))
    r = await a.check_access(allow_generation=False)
    assert (r.server_reached, r.access_ok, r.model_listed, r.answer_ok) == (True, True, True, None)
    r = await a.check_access(probe_model="nope", allow_generation=False)
    assert r.model_listed is False and r.answer_ok is None
    # probe without a chosen model: the first listed model is used and reported
    r = await a.check_access(allow_generation=True)
    assert r.answer_ok is True and r.details["probe_model"] == "m1" and r.details["probe_answer"] == "OK" and r.at
    assert fake.requests[-1].body["model"] == "m1"
    await a.aclose()
    # wrong key: server reached, access refused, no probe
    fake.on("GET", "/v1/models", Scripted(401, "application/json", b'{"error":{"message":"bad key"}}'))
    b = ChatCompletionsAdapter(cfg(fake, api_key="wrong"))
    r = await b.check_access(allow_generation=True)
    assert r.server_reached is True and r.access_ok is False and r.answer_ok is None and r.error["code"] == "unauthorized"
    await b.aclose()
    # server down: nothing reached
    down = ChatCompletionsAdapter(cfg(fake, base_url="http://127.0.0.1:9/v1", timeout_connect_s=1.0))
    r = await down.check_access(allow_generation=True)
    assert r.server_reached is False and r.access_ok is None and r.error["code"] in ("network", "timeout")
    await down.aclose()

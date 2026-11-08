"""
A deterministic OpenAI-compatible server for tests.

It measures memory mechanics only. Its answers follow simple text rules and say
nothing about how a real model would perform, so every report built on it has
to say so.
"""
from __future__ import annotations

import json
import re
import socket
import threading
import time
from typing import Optional

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.core.hashing import hash_embed

_WORD = re.compile(r"[a-z0-9]+")
_ENTITY = re.compile(r"\b[A-Z][a-z0-9]+(?: [A-Z][a-z0-9]+)*\b")
_TAGGED_LINE = re.compile(r"\[n(\d+)\][^\n]*")


def _words(text: str) -> set[str]:
    return {word for word in _WORD.findall(text.lower()) if len(word) > 2}


def _content(message: dict) -> str:
    content = message.get("content") or ""
    if isinstance(content, list):
        return " ".join(part.get("text", "") for part in content if part.get("type") == "text")
    return content


def _extract(text: str) -> str:
    nodes: dict[str, dict] = {}
    edges = []
    for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
        found = list(dict.fromkeys(_ENTITY.findall(sentence)))
        for label in found:
            nodes.setdefault(label, {"label": label, "type": "CONCEPT", "properties": {"source_text": sentence}})
        if len(found) >= 2:
            start, end = sentence.index(found[0]) + len(found[0]), sentence.index(found[1], len(found[0]))
            relation = "_".join(_WORD.findall(sentence[start:end].lower())).upper() or "RELATED_TO"
            edges.append(
                {"source": found[0], "target": found[1], "relation": relation, "properties": {"source_text": sentence}}
            )
    return json.dumps({"nodes": list(nodes.values()), "edges": edges})


def _answer(user: str) -> str:
    match = re.search(r"Question:\s*(.+)", user)
    question = _words(match.group(1)) if match else set()
    scored = []
    for line in _TAGGED_LINE.finditer(user):
        scored.append((len(question & _words(line.group(0))), -int(line.group(1)), line.group(1)))
    scored.sort(reverse=True)
    if not scored or scored[0][0] == 0:
        return "The graph data does not contain the answer."
    return " ".join(f"Evidence found [n{number}]." for _, _, number in scored[:2])


class OpenAIStub:
    def __init__(self) -> None:
        self.chat_calls: list[list[dict]] = []
        self.embedding_calls = 0
        self.fail_with: Optional[int] = None
        self.dimensions = 1536
        self._server: Optional[uvicorn.Server] = None
        self.base_url = ""
        self.app = self._build_app()

    def reply(self, messages: list[dict]) -> str:
        system = " ".join(_content(m) for m in messages if m.get("role") == "system")
        user = _content(next((m for m in reversed(messages) if m.get("role") == "user"), {}))
        everything = f"{system} {user}"
        if '"is_state"' in user:
            return json.dumps({"is_state": bool(re.search(r"\b(is|now|current)\b", user.split("Sentence:")[-1]))})
        if "superseded_ids" in user:
            existing = user.split("EXISTING facts:")[-1]
            return json.dumps({"superseded_ids": re.findall(r"- id=([^:\s]+):", existing)})
        if "is_sufficient" in everything:
            return json.dumps({"is_sufficient": True, "confidence": 0.95, "next_search_query": None, "feedback": []})
        if "Passage:" in user:
            return user.split("Passage:")[-1].split("Answer:")[0].strip()[:80]
        if "SLEEP-LESSON" in user:
            return "Check the cited source before answering this kind of question."
        if "SLEEP-SKILL" in user:
            return "1. Find the entity. 2. Read its facts. 3. Answer with citations."
        if "SLEEP-COMPACT" in user:
            return "Summary of " + str(user.count("\n- ")) + " events."
        if "Evidence lines start with a tag" in system:
            return _answer(user)
        if "nodes" in system.lower() and "edges" in system.lower():
            return _extract(user)
        return "ok"

    def _build_app(self) -> FastAPI:
        app = FastAPI()

        @app.post("/v1/embeddings")
        @app.post("/embeddings")
        async def embeddings(request: Request):
            self.embedding_calls += 1
            if self.fail_with:
                return JSONResponse({"error": {"message": "stub failure"}}, status_code=self.fail_with)
            body = await request.json()
            inputs = body["input"] if isinstance(body["input"], list) else [body["input"]]
            data = [
                {"object": "embedding", "index": index, "embedding": self._embed(text)}
                for index, text in enumerate(inputs)
            ]
            usage = {"prompt_tokens": 1, "total_tokens": 1}
            return {"object": "list", "model": body.get("model", "stub"), "data": data, "usage": usage}

        @app.post("/v1/chat/completions")
        @app.post("/chat/completions")
        async def chat(request: Request):
            body = await request.json()
            self.chat_calls.append(body["messages"])
            if self.fail_with:
                return JSONResponse({"error": {"message": "stub failure"}}, status_code=self.fail_with)
            text = self.reply(body["messages"])
            base = {"id": "stub", "created": int(time.time()), "model": body.get("model", "stub")}
            if body.get("stream"):
                return StreamingResponse(self._stream(text, base), media_type="text/event-stream")
            words = len(text.split())
            usage = {"prompt_tokens": 10, "completion_tokens": words, "total_tokens": 10 + words}
            return {
                **base,
                "object": "chat.completion",
                "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}],
                "usage": usage,
            }

        return app

    @staticmethod
    async def _stream(text, base):
        for word in re.findall(r"\S+\s*", text):
            delta = {"index": 0, "delta": {"role": "assistant", "content": word}, "finish_reason": None}
            yield f"data: {json.dumps({**base, 'object': 'chat.completion.chunk', 'choices': [delta]})}\n\n"
        done = {"index": 0, "delta": {}, "finish_reason": "stop"}
        yield f"data: {json.dumps({**base, 'object': 'chat.completion.chunk', 'choices': [done]})}\n\n"
        yield "data: [DONE]\n\n"

    def _embed(self, text: str) -> list[float]:
        base = hash_embed(text)
        return [base[index % len(base)] for index in range(self.dimensions)]

    def start(self) -> None:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        config = uvicorn.Config(self.app, host="127.0.0.1", port=port, log_level="error", lifespan="off")
        self._server = uvicorn.Server(config)
        threading.Thread(target=self._server.run, daemon=True).start()
        deadline = time.monotonic() + 10
        while not self._server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("OpenAI stub did not start")
            time.sleep(0.01)
        self.base_url = f"http://127.0.0.1:{port}/v1"

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True

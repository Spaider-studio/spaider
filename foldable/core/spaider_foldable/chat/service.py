"""ChatService: one streaming generation at a time per conversation.

Flow for ``send``:
  1. persist the user message, create the assistant message (status streaming)
  2. retrieve memory context (status event "retrieving")
  3. assemble messages within the real context window (token counts from the
     runtime's /tokenize endpoint, char estimate as fallback)
  4. stream tokens (status "generating"); persist partial content every ~1 s
     so a crash keeps what was generated; stop → status "stopped"
  5. final persist with sources + usage, enqueue the user message for memory
     extraction (background worker, status "memorizing" is reported by the
     worker's presence on the next turn)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import AsyncIterator, Optional

from ..inference.manager import InferenceManager
from ..memory.ingest import IngestQueue
from ..memory.retrieve import MemoryRetriever
from ..store.conversations import ConversationStore
from ..store.settings import SettingsStore
from .prompts import SYSTEM_PROMPT, memory_block

logger = logging.getLogger(__name__)

CHARS_PER_TOKEN = 3.2  # conservative estimate for German/English mix


class ChatBusy(RuntimeError):
    pass


class NoModel(RuntimeError):
    pass


class RuntimeNotReady(RuntimeError):
    pass


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


class ChatService:
    def __init__(
        self,
        conversations: ConversationStore,
        retriever: MemoryRetriever,
        inference: InferenceManager,
        settings: SettingsStore,
        ingest_queue: IngestQueue,
        chat_idle: asyncio.Event,
    ):
        self.conversations = conversations
        self.retriever = retriever
        self.inference = inference
        self.settings = settings
        self.ingest_queue = ingest_queue
        self.chat_idle = chat_idle
        self._active: dict[str, asyncio.Event] = {}  # conversation_id -> stop event
        self._global = asyncio.Lock()
        self.chat_idle.set()

    @property
    def active_streams(self) -> int:
        return len(self._active)

    async def stop(self, conversation_id: str) -> bool:
        ev = self._active.get(conversation_id)
        if ev is None:
            return False
        ev.set()
        return True

    async def stop_all(self) -> None:
        for ev in list(self._active.values()):
            ev.set()
        for _ in range(50):
            if not self._active:
                break
            await asyncio.sleep(0.1)

    def recover_after_crash(self) -> None:
        for m in self.conversations.streaming_messages():
            self.conversations.update_message(
                m["id"], status="stopped", error="Antwort wurde durch einen Neustart unterbrochen."
            )

    async def _count_tokens(self, client, text: str) -> int:
        n = await client.tokenize_count(text)
        return n if n is not None else int(len(text) / CHARS_PER_TOKEN) + 1

    async def send(self, conversation_id: str, content: str) -> AsyncIterator[str]:
        conv = self.conversations.get(conversation_id)
        if conversation_id in self._active:
            raise ChatBusy()
        slot = self.inference.chat
        if slot.model_id is None and slot.state == "stopped":
            raise NoModel()
        client = slot.client
        if client is None:
            raise RuntimeNotReady(slot.error or slot.state)
        prefs = self.settings.load()
        content = content.strip()
        stop = asyncio.Event()
        self._active[conversation_id] = stop
        self.chat_idle.clear()
        user_msg = self.conversations.add_message(conversation_id, "user", content)
        assistant = self.conversations.add_message(
            conversation_id, "assistant", "", status="streaming", model_id=slot.model_id
        )
        if conv["message_count"] == 0 and conv["title"] == "Neues Gespräch":
            self.conversations.rename(
                conversation_id, content[:60].replace("\n", " ") + ("…" if len(content) > 60 else "")
            )
        yield sse("message", {"user_message": user_msg, "assistant_message": assistant})
        text_parts: list[str] = []
        status = "complete"
        error: Optional[str] = None
        usage: dict = {}
        sources: list[dict] = []
        try:
            yield sse("status", {"phase": "retrieving", "message_de": "Gedächtnis wird durchsucht …"})
            ctx_budget_chars = int(prefs.context_tokens * 0.25 * CHARS_PER_TOKEN)
            try:
                retrieved = await asyncio.wait_for(
                    self.retriever.retrieve(content, max_chars=ctx_budget_chars), timeout=30
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("retrieval failed: %s", exc)
                retrieved = None
            if retrieved is not None:
                sources = retrieved.sources
                yield sse("sources", {"sources": sources})
            system = SYSTEM_PROMPT
            if prefs.system_prompt_extra.strip():
                system += "\n\nZusätzliche Hinweise des Nutzers:\n" + prefs.system_prompt_extra.strip()
            system += "\n\n" + memory_block(retrieved.context_text if retrieved else "")
            history = self._history_within_budget(
                conversation_id,
                exclude_ids={assistant["id"]},
                budget_tokens=prefs.context_tokens
                - prefs.max_answer_tokens
                - await self._count_tokens(client, system)
                - 64,
            )
            messages = [{"role": "system", "content": system}] + history
            yield sse("status", {"phase": "generating", "message_de": "Antwort wird erzeugt …"})
            last_persist = time.monotonic()
            async for chunk in client.stream_chat(
                messages,
                temperature=prefs.temperature,
                max_tokens=prefs.max_answer_tokens,
                enable_thinking=prefs.thinking_enabled,
                stop_event=stop,
            ):
                if chunk.done:
                    usage = chunk.usage
                    break
                if chunk.reasoning:
                    yield sse("thinking", {"text": chunk.reasoning})
                if chunk.text:
                    text_parts.append(chunk.text)
                    yield sse("token", {"text": chunk.text})
                if time.monotonic() - last_persist > 1.0:
                    self.conversations.update_message(assistant["id"], content="".join(text_parts))
                    last_persist = time.monotonic()
            if stop.is_set():
                status = "stopped"
        except asyncio.CancelledError:
            status = "stopped"
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("generation failed")
            status = "error"
            error = str(exc)[:500]
            yield sse(
                "error", {"detail": "Die Antwort konnte nicht erzeugt werden: " + error, "code": "generation_failed"}
            )
        finally:
            final = self.conversations.update_message(
                assistant["id"],
                content="".join(text_parts),
                status=status,
                sources=sources,
                usage=_usage_api(usage) if usage else None,
                error=error,
            )
            self.conversations.touch(conversation_id, slot.model_id)
            self._active.pop(conversation_id, None)
            if not self._active:
                self.chat_idle.set()
            if prefs.memory_auto_extract and len(content) >= 12:
                self.ingest_queue.enqueue("message", user_msg["id"])
        if status != "error":
            yield sse("done", {"message": final})

    def _history_within_budget(self, conversation_id: str, *, exclude_ids: set[str], budget_tokens: int) -> list[dict]:
        msgs = [
            m
            for m in self.conversations.messages(conversation_id)
            if m["id"] not in exclude_ids and m["content"].strip()
        ]
        chosen: list[dict] = []
        used = 0
        for m in reversed(msgs):
            if m["role"] not in ("user", "assistant"):
                continue
            cost = int(len(m["content"]) / CHARS_PER_TOKEN) + 4
            if chosen and used + cost > max(budget_tokens, 256):
                break
            chosen.append({"role": m["role"], "content": m["content"]})
            used += cost
        chosen.reverse()
        # Always keep the latest user message even if it alone exceeds the budget (it gets truncated).
        if chosen and chosen[-1]["role"] == "user" and used > budget_tokens:
            chosen[-1]["content"] = chosen[-1]["content"][: int(max(budget_tokens, 256) * CHARS_PER_TOKEN)]
        return chosen


def _usage_api(usage: dict) -> dict:
    return {
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "time_to_first_token_ms": int(usage.get("time_to_first_token_ms") or 0),
        "tokens_per_second": float(usage.get("tokens_per_second") or 0.0),
        "prompt_tokens_per_second": float(usage.get("prompt_tokens_per_second") or 0.0),
        "finish_reason": usage.get("finish_reason"),
    }

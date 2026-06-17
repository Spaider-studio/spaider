"""
URL Connector — incremental fetch-and-parse using httpx + Trafilatura.

Incremental sync protocol
--------------------------
For each URL in the batch the connector checks ``run_state.source_states``
for a previously stored ``etag`` or ``last_modified`` value and sends the
appropriate conditional HTTP header:

  * ``If-None-Match``    — sent when an ETag was saved from the previous run.
  * ``If-Modified-Since`` — sent when a Last-Modified timestamp was saved.

Server response interpretation:
  * HTTP 304 (Not Modified) — content unchanged; yield nothing for this URL.
  * HTTP 200               — new content; parse, update RunState, yield record.
  * HTTP 4xx / 5xx / error — log, route to DLQ, continue to next URL.

The caller must persist ``run_state`` after the generator is exhausted so
the updated ETags/Last-Modified values survive across process restarts.

Usage
-----
    from app.connectors.trafilatura_url_connector import URLConnector
    from app.connectors import RunState

    connector = URLConnector()
    run_state = load_run_state_from_db(connector.connector_id, agent_id)

    async for record in connector.run(agent_id, run_state, urls=[...]):
        await ingest_pipeline(record)

    save_run_state_to_db(run_state)  # persist updated ETags
"""
from __future__ import annotations

import logging
from typing import AsyncGenerator

import httpx

from app.connectors import BaseConnector, ConnectorRecord, RunState, send_to_dlq
from app.services.parser_service import parse

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT = 15.0   # seconds — balances slow CDNs against stalling the run
_MAX_CONTENT_BYTES = 10 * 1024 * 1024  # 10 MB safety cap — prevents OOM on giant pages


class URLConnector(BaseConnector):
    """
    Fetches a list of URLs and yields one ``ConnectorRecord`` per URL whose
    content has changed since the last run (or per URL on a first run).

    A single ``httpx.AsyncClient`` is shared across all URLs in a batch to
    reuse TCP connections and respect keep-alive semantics.
    """

    connector_id = "url"

    async def run(  # type: ignore[override]
        self,
        agent_id: str,
        run_state: RunState,
        *,
        urls: list[str],
    ) -> AsyncGenerator[ConnectorRecord, None]:
        """
        Parameters
        ----------
        agent_id : str
            Owning agent namespace — stamped on every yielded record.
        run_state : RunState
            Read before each request for conditional headers; mutated in-place
            after every HTTP 200 response.  Caller must persist after the run.
        urls : list[str]
            Absolute HTTP/HTTPS URLs to fetch.
        """
        if not urls:
            logger.debug("url_connector: no URLs supplied — yielding nothing.")
            return

        logger.info(
            "url_connector: starting batch agent_id=%s url_count=%d",
            agent_id, len(urls),
        )

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(_HTTP_TIMEOUT),
            follow_redirects=True,
            headers={
                "User-Agent": "SpAIder-URLConnector/1.0 (+https://spaider.ai/bot)",
            },
        ) as client:
            for url in urls:
                await self._fetch_one(
                    client=client,
                    url=url,
                    agent_id=agent_id,
                    run_state=run_state,
                )
                # _fetch_one is a regular coroutine — we must yield from outside.
                # Re-implement inline so we can yield inside the async-with block.

        # Implementation note: Python's async generator protocol does not allow
        # `yield` inside a helper coroutine called from a generator.  The fetch
        # logic is therefore inlined in the loop below rather than factored out.
        #
        # The async-with block above was illustrative.  The real loop follows.

    # ------------------------------------------------------------------
    # Actual generator body (inlined for yield-inside-async-with compat)
    # ------------------------------------------------------------------

    # Override __init_subclass__ trick is unnecessary — Python 3.11 allows
    # `yield` inside `async with` blocks in async generators natively.
    # We simply re-declare `run` as the true generator below.

    async def run(  # type: ignore[override]  # noqa: F811 — intentional redefinition
        self,
        agent_id: str,
        run_state: RunState,
        *,
        urls: list[str],
    ) -> AsyncGenerator[ConnectorRecord, None]:
        """Async-generator implementation (yield inside async-with is valid in 3.11+)."""
        if not urls:
            logger.debug("url_connector: no URLs supplied — yielding nothing.")
            return

        logger.info(
            "url_connector: starting batch agent_id=%s url_count=%d",
            agent_id, len(urls),
        )

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(_HTTP_TIMEOUT),
            follow_redirects=True,
            headers={
                "User-Agent": "SpAIder-URLConnector/1.0 (+https://spaider.ai/bot)",
            },
        ) as client:

            for url in urls:
                # ── Build conditional request headers ─────────────────────
                source_state = run_state.source_states.get(url, {})
                request_headers: dict[str, str] = {}

                if etag := source_state.get("etag"):
                    request_headers["If-None-Match"] = etag
                    logger.debug("url_connector: url=%r sending If-None-Match=%r", url, etag)

                if last_modified := source_state.get("last_modified"):
                    request_headers["If-Modified-Since"] = last_modified
                    logger.debug(
                        "url_connector: url=%r sending If-Modified-Since=%r",
                        url, last_modified,
                    )

                # ── HTTP fetch ────────────────────────────────────────────
                try:
                    response = await client.get(url, headers=request_headers)
                except httpx.TimeoutException as exc:
                    logger.error("url_connector: timeout url=%r: %s", url, exc)
                    await send_to_dlq(
                        payload={"connector_id": self.connector_id, "source_uri": url, "agent_id": agent_id},
                        connector_id=self.connector_id,
                        source_uri=url,
                        agent_id=agent_id,
                        error=f"Timeout after {_HTTP_TIMEOUT}s: {exc}",
                        reason="http_timeout",
                    )
                    continue
                except httpx.RequestError as exc:
                    logger.error("url_connector: request error url=%r: %s", url, exc)
                    await send_to_dlq(
                        payload={"connector_id": self.connector_id, "source_uri": url, "agent_id": agent_id},
                        connector_id=self.connector_id,
                        source_uri=url,
                        agent_id=agent_id,
                        error=str(exc),
                        reason="http_error",
                    )
                    continue

                # ── Handle HTTP 304 — content unchanged ───────────────────
                if response.status_code == 304:
                    logger.debug(
                        "url_connector: url=%r HTTP 304 Not Modified — skipping.", url,
                    )
                    continue  # yield nothing for this URL

                # ── Handle HTTP errors ────────────────────────────────────
                if response.status_code >= 400:
                    error_msg = f"HTTP {response.status_code}: {response.reason_phrase}"
                    logger.error("url_connector: url=%r %s", url, error_msg)
                    await send_to_dlq(
                        payload={
                            "connector_id": self.connector_id,
                            "source_uri": url,
                            "agent_id": agent_id,
                            "http_status": response.status_code,
                        },
                        connector_id=self.connector_id,
                        source_uri=url,
                        agent_id=agent_id,
                        error=error_msg,
                        reason="http_error",
                    )
                    continue

                # ── HTTP 200 — parse new content ──────────────────────────
                raw_content = response.content
                if len(raw_content) > _MAX_CONTENT_BYTES:
                    logger.warning(
                        "url_connector: url=%r content too large (%d bytes > %d cap) — truncating.",
                        url, len(raw_content), _MAX_CONTENT_BYTES,
                    )
                    raw_content = raw_content[:_MAX_CONTENT_BYTES]

                # Determine MIME type from response headers; default to text/html
                content_type = response.headers.get("content-type", "text/html")
                base_mime = content_type.split(";")[0].strip().lower() or "text/html"

                try:
                    parse_result = await parse(raw_content, base_mime)
                except Exception as exc:
                    logger.error(
                        "url_connector: parse failed url=%r mime=%r: %s",
                        url, base_mime, exc,
                    )
                    await send_to_dlq(
                        payload={
                            "connector_id": self.connector_id,
                            "source_uri": url,
                            "agent_id": agent_id,
                            "mime_type": base_mime,
                            "http_status": response.status_code,
                        },
                        connector_id=self.connector_id,
                        source_uri=url,
                        agent_id=agent_id,
                        error=str(exc),
                        reason="parse_error",
                    )
                    continue

                if not parse_result.text.strip():
                    logger.warning(
                        "url_connector: empty text after parse url=%r — skipping.", url
                    )
                    continue

                # ── Update RunState with new conditional headers ──────────
                new_source_state: dict[str, str] = {}
                if new_etag := response.headers.get("etag"):
                    new_source_state["etag"] = new_etag
                if new_last_modified := response.headers.get("last-modified"):
                    new_source_state["last_modified"] = new_last_modified
                # Always persist even if empty — clears stale values on
                # servers that stopped sending ETags.
                run_state.source_states[url] = new_source_state

                logger.debug(
                    "url_connector: parsed url=%r mime=%r chars=%d etag=%r",
                    url, base_mime, len(parse_result.text),
                    new_source_state.get("etag"),
                )

                yield ConnectorRecord(
                    connector_id=self.connector_id,
                    source_uri=url,
                    text=parse_result.text,
                    mime_type=base_mime,
                    hints=parse_result.hints,
                    agent_id=agent_id,
                    metadata={
                        "http_status": response.status_code,
                        "content_type": content_type,
                        "url": url,
                    },
                )

        logger.info(
            "url_connector: batch complete agent_id=%s url_count=%d",
            agent_id, len(urls),
        )

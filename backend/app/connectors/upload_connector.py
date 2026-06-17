"""
Upload Connector — multi-file batch ingestion via the unified parser.

Each file in the batch is parsed independently.  On parse failure the item
is routed to the DLQ with a ``dlq-connector-id: upload`` header and the
loop continues to the next file — a single bad PDF never aborts the batch.

Usage (from an API route)
--------------------------
    from app.connectors.upload_connector import UploadConnector
    from app.connectors import RunState

    connector = UploadConnector()
    run_state = RunState(connector_id=connector.connector_id)

    files = [
        ("report.pdf",  pdf_bytes,  "application/pdf"),
        ("notes.md",    md_bytes,   "text/markdown"),
        ("page.html",   html_bytes, "text/html"),
    ]

    async for record in connector.run(agent_id, run_state, files=files):
        # record: ConnectorRecord with .text ready for SemanticCompressor
        ...

    # run_state is unchanged after upload runs (no incremental-sync state)
"""
from __future__ import annotations

import logging
from typing import AsyncGenerator

from app.connectors import BaseConnector, ConnectorRecord, RunState, send_to_dlq
from app.services.parser_service import parse

logger = logging.getLogger(__name__)

# MIME types accepted at the API layer — used for fast validation before
# the connector runs.  The parser_service handles anything in this set.
ACCEPTED_MIME_TYPES: frozenset[str] = frozenset({
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # DOCX
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",  # PPTX
    "application/msword",       # legacy .doc
    "application/vnd.ms-powerpoint",  # legacy .ppt
    "text/html",
    "application/xhtml+xml",
    "text/markdown",
    "text/x-markdown",
    "text/plain",
})


class UploadConnector(BaseConnector):
    """
    Accepts a list of ``(filename, content_bytes, mime_type)`` tuples and
    yields one ``ConnectorRecord`` per successfully parsed file.

    File-level errors are isolated: a corrupt PDF does not stop a valid
    Markdown file later in the same batch.
    """

    connector_id = "upload"

    async def run(  # type: ignore[override]
        self,
        agent_id: str,
        run_state: RunState,
        *,
        files: list[tuple[str, bytes, str]],
    ) -> AsyncGenerator[ConnectorRecord, None]:
        """
        Parameters
        ----------
        agent_id : str
            Owning agent namespace — stamped on every yielded record.
        run_state : RunState
            Provided for interface consistency; upload runs are one-shot so
            no incremental state is read or written.
        files : list[tuple[str, bytes, str]]
            Each tuple is ``(filename, raw_bytes, mime_type)``.
            Caller is responsible for reading UploadFile bytes before passing here.
        """
        if not files:
            logger.debug("upload_connector: no files supplied — yielding nothing.")
            return

        logger.info(
            "upload_connector: starting batch agent_id=%s file_count=%d",
            agent_id, len(files),
        )

        for filename, content, mime_type in files:
            # Normalise MIME: strip charset/boundary params
            base_mime = mime_type.split(";")[0].strip().lower()

            # ── Parse ──────────────────────────────────────────────────────
            try:
                parse_result = await parse(content, base_mime)
            except Exception as exc:
                logger.error(
                    "upload_connector: parse failed file=%r mime=%r agent=%s — routing to DLQ: %s",
                    filename, base_mime, agent_id, exc,
                )
                await send_to_dlq(
                    payload={
                        "connector_id": self.connector_id,
                        "source_uri": filename,
                        "agent_id": agent_id,
                        "mime_type": base_mime,
                        "error": str(exc),
                        "content_length": len(content),
                    },
                    connector_id=self.connector_id,
                    source_uri=filename,
                    agent_id=agent_id,
                    error=str(exc),
                    reason="parse_error",
                )
                continue

            # Silently skip empty extractions (scanned images with no OCR text, etc.)
            if not parse_result.text.strip():
                logger.warning(
                    "upload_connector: empty text after parse file=%r mime=%r — skipping.",
                    filename, base_mime,
                )
                continue

            logger.debug(
                "upload_connector: parsed file=%r mime=%r chars=%d",
                filename, base_mime, len(parse_result.text),
            )

            yield ConnectorRecord(
                connector_id=self.connector_id,
                source_uri=filename,
                text=parse_result.text,
                mime_type=base_mime,
                hints=parse_result.hints,
                agent_id=agent_id,
                metadata={"original_filename": filename},
            )

        logger.info(
            "upload_connector: batch complete agent_id=%s file_count=%d",
            agent_id, len(files),
        )

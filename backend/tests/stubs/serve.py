"""Runs the deterministic OpenAI stub as a standalone server.

The test rig points the backend at it, so the whole stack can be driven in a
browser without a real model or any API cost. Its answers follow simple text
rules and say nothing about how a real model performs.

    python -m tests.stubs.serve --host 0.0.0.0 --port 9999
"""
from __future__ import annotations

import argparse

import uvicorn

from app.core.hashing import HASH_EMBEDDING_DIM
from tests.stubs.openai_stub import OpenAIStub


def build_stub(dimensions: int = HASH_EMBEDDING_DIM) -> OpenAIStub:
    stub = OpenAIStub()
    stub.dimensions = dimensions

    @stub.app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    return stub


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--dimensions", type=int, default=HASH_EMBEDDING_DIM)
    args = parser.parse_args()
    uvicorn.run(build_stub(args.dimensions).app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()

"""Entry point: ``python -m spaider_foldable --app-root <dir>`` (dev) or the
frozen ``spaider-foldable-core.exe`` (packaged).

Prints exactly one JSON line to stdout when the API is ready:
``{"event": "ready", "port": <int>}``. Everything else goes to stderr and to
``data/logs/core.log``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import logging.handlers
import os
import signal
import sys
from pathlib import Path

from . import __version__
from .bootstrap import bootstrap


def _default_runtime_dir() -> Path | None:
    env = os.environ.get("SPAIDER_FOLDABLE_RUNTIME_DIR")
    if env:
        return Path(env)
    if getattr(sys, "frozen", False):
        # <app>/resources/core/spaider-foldable-core.exe -> <app>/resources/runtime/llama
        return Path(sys.executable).resolve().parent.parent / "runtime" / "llama"
    return None


def _default_app_root() -> Path | None:
    env = os.environ.get("SPAIDER_FOLDABLE_ROOT")
    if env:
        return Path(env)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent.parent.parent
    return None


def _configure_logging(logs_dir: Path, level: str) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    fh = logging.handlers.RotatingFileHandler(
        logs_dir / "core.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    root.addHandler(sh)
    for noisy in ("httpx", "httpcore", "LiteLLM", "litellm", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="spaider-foldable-core")
    parser.add_argument("--app-root", type=Path, default=None, help="application root (folder containing SpAIder.exe)")
    parser.add_argument("--runtime-dir", type=Path, default=None, help="directory with llama-server")
    parser.add_argument("--port", type=int, default=0, help="loopback port (0 = free port)")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--mcp-stdio", action="store_true", help="run as MCP stdio bridge for one connector (no API server)")
    parser.add_argument("--connector", type=Path, default=None, help="connector file (data/connectors/<id>.json) for --mcp-stdio")
    args = parser.parse_args(argv)

    if args.mcp_stdio:
        from .connectors.bridge import run_bridge

        return run_bridge(args.connector, log_level=args.log_level)

    app_root = args.app_root or _default_app_root()
    if app_root is None:
        print("error: --app-root (or SPAIDER_FOLDABLE_ROOT) is required", file=sys.stderr)
        return 2
    runtime_dir = args.runtime_dir or _default_runtime_dir()
    if runtime_dir is None:
        print("error: --runtime-dir (or SPAIDER_FOLDABLE_RUNTIME_DIR) is required", file=sys.stderr)
        return 2
    token = os.environ.get("SPAIDER_FOLDABLE_TOKEN", "")
    if len(token) < 16:
        print("error: SPAIDER_FOLDABLE_TOKEN (>= 16 chars) must be set in the environment", file=sys.stderr)
        return 2

    bootstrap()
    from .api.app import create_app  # noqa: E402
    from .context import AppContext  # noqa: E402
    from .inference.runtime import free_port  # noqa: E402
    from .lock import AlreadyRunningError  # noqa: E402
    from .paths import PortablePathError, PortablePaths  # noqa: E402

    try:
        paths = PortablePaths.from_app_root(app_root.resolve())
        paths.ensure()
    except (PortablePathError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    _configure_logging(paths.logs_dir, args.log_level)
    os.chdir(paths.data_dir)
    log = logging.getLogger("spaider_foldable.main")
    log.info("starting core %s app_root=%s runtime=%s pid=%s", __version__, paths.app_root, runtime_dir, os.getpid())

    import uvicorn

    exit_code = {"code": 0}

    async def serve() -> None:
        loop = asyncio.get_running_loop()
        server_holder: dict = {}

        def on_exit() -> None:
            srv = server_holder.get("server")
            if srv is not None:
                srv.should_exit = True

        runtime_dirs = {"cpu": Path(runtime_dir).resolve()}
        vk = os.environ.get("SPAIDER_FOLDABLE_RUNTIME_DIR_VULKAN") or str(
            Path(runtime_dir).resolve().parent / "llama-vulkan"
        )
        if Path(vk).exists():
            runtime_dirs["vulkan"] = Path(vk).resolve()
        from .observe.uia import configure_comtypes_cache

        configure_comtypes_cache(paths.data_dir / "cache")
        from .profiles import ProfileManager  # noqa: E402

        manager = ProfileManager(paths, runtime_dirs, lambda pp, lock: AppContext(pp, runtime_dirs, on_exit=on_exit, lock=lock), on_exit=on_exit)
        try:
            await loop.run_in_executor(None, manager.start_sync)
        except AlreadyRunningError as exc:
            print(json.dumps({"event": "error", "code": "already_running", "detail": str(exc)}), flush=True)
            exit_code["code"] = 3
            return
        except Exception as exc:  # noqa: BLE001
            log.exception("startup failed")
            print(json.dumps({"event": "error", "code": "startup_failed", "detail": str(exc)}), flush=True)
            exit_code["code"] = 4
            return
        ctx = manager.ctx
        await manager.start_async()
        app = create_app(ctx, token)
        app.state.manager = manager
        manager.app = app
        port = args.port or free_port()
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None, access_log=False, lifespan="off")
        server = uvicorn.Server(config)
        server_holder["server"] = server
        task = asyncio.create_task(server.serve())
        while not server.started and not task.done():
            await asyncio.sleep(0.05)
        if task.done():
            log.error("server failed to start")
            exit_code["code"] = 5
            await manager.ctx.shutdown()
            return
        print(json.dumps({"event": "ready", "port": port, "profile_id": manager.active_id}), flush=True)
        log.info("API ready on 127.0.0.1:%d", port)
        # discovery file for the MCP stdio bridges (shared host dir; no secrets inside)
        core_json = paths.host_dir / "core.json"
        try:
            paths.host_dir.mkdir(parents=True, exist_ok=True)
            core_json.write_text(
                json.dumps({"port": port, "pid": os.getpid(), "version": __version__, "profile_id": manager.active_id}),
                encoding="utf-8",
            )
        except OSError as exc:
            log.warning("could not write %s: %s", core_json, exc)

        def _signal_handler(*_):
            manager.ctx.request_shutdown()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, _signal_handler)
            except (ValueError, OSError):
                pass
        if sys.platform == "win32":
            try:
                signal.signal(signal.SIGBREAK, _signal_handler)  # type: ignore[attr-defined]
            except (ValueError, OSError):
                pass
        await task
        try:
            core_json.unlink(missing_ok=True)
        except OSError:
            pass
        if not manager.ctx.shutting_down:
            await manager.ctx.shutdown()

    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass
    log.info("core exited with code %d", exit_code["code"])
    return exit_code["code"]


if __name__ == "__main__":
    sys.exit(main())

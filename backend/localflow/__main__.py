"""Entry point for the LocalFlow backend process.

Started by the Tauri shell as a child process.  Reads the port/token from the
command line (or generates them), prints ``LOCALFLOW_READY {...}`` on stdout so
the parent can connect, then serves until killed.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

# CUDA DLL directories must be registered before anything imports CTranslate2.
from .asr.gpu import prepare_cuda

prepare_cuda()

from . import paths  # noqa: E402
from .server import RpcServer  # noqa: E402
from .service import LocalFlowService  # noqa: E402


def configure_logging(level: str, log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"
    )
    file_handler = RotatingFileHandler(
        log_dir / "backend.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # stderr only: stdout carries the READY handshake and must stay clean.
    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(formatter)
    root.addHandler(stream)

    for noisy in ("websockets", "httpx", "httpcore", "huggingface_hub", "urllib3", "filelock"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="localflow-backend")
    parser.add_argument("--port", type=int, default=0, help="0 selects an ephemeral port")
    parser.add_argument("--token", default=os.environ.get("LOCALFLOW_TOKEN", ""))
    parser.add_argument("--data-dir", default=os.environ.get("LOCALFLOW_DATA_DIR", ""))
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--no-warm", action="store_true", help="skip model warm-up on start")
    return parser.parse_args(argv)


async def _run(server: RpcServer) -> None:
    stop = asyncio.Event()

    def request_stop(*_: object) -> None:
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, request_stop)
        except (NotImplementedError, ValueError):
            signal.signal(sig, request_stop)

    serve_task = asyncio.create_task(server.serve())
    stop_task = asyncio.create_task(stop.wait())
    done, pending = await asyncio.wait(
        {serve_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
    )
    for task in pending:
        task.cancel()
    for task in done:
        if task is serve_task and not task.cancelled():
            exc = task.exception()
            if exc:
                raise exc


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    data_dir = Path(args.data_dir).expanduser() if args.data_dir else paths.DATA_DIR
    configure_logging(args.log_level, data_dir / "logs")
    log = logging.getLogger("localflow")

    service = LocalFlowService(data_dir=data_dir)
    server = RpcServer(service, port=args.port, token=args.token)
    try:
        service.start(warm=not args.no_warm)
    except Exception:
        log.exception("Startup failed; continuing so the UI can report it")

    try:
        asyncio.run(_run(server))
    except KeyboardInterrupt:
        pass
    except Exception:
        log.exception("Backend crashed")
        return 1
    finally:
        log.info("Shutting down")
        try:
            service.shutdown()
        except Exception:
            log.debug("Shutdown error", exc_info=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

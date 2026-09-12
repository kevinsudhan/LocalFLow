"""Local JSON-RPC over WebSocket.

Security posture:

* binds to ``127.0.0.1`` only - never a routable interface;
* an ephemeral port, printed to stdout for the parent process to read;
* a random per-launch token that every connection must present;
* an explicit method allowlist - there is no dynamic dispatch to arbitrary
  attributes, so a crafted message cannot reach anything not listed here.

Long-running calls (transcription, model loading) run on a worker thread so the
event loop keeps streaming audio levels and partial transcripts.
"""
from __future__ import annotations

import asyncio
import functools
import inspect
import json
import logging
import secrets
from typing import Any, Callable

import websockets
from websockets.server import WebSocketServerProtocol

from .service import LocalFlowService

log = logging.getLogger(__name__)

MAX_MESSAGE_BYTES = 1_000_000


class RpcError(Exception):
    def __init__(self, code: str, message: str, detail: str = ""):
        super().__init__(message)
        self.code = code
        self.detail = detail


def _methods(service: LocalFlowService) -> dict[str, Callable[..., Any]]:
    """The explicit allowlist of callable methods."""
    return {
        "system.ping": lambda: {"pong": True},
        "system.info": service.diagnostics,
        "system.hardware": service.hardware,
        "system.catalogues": service.catalogues,
        "system.privacy": service.privacy_report,
        "system.export": service.export_all,
        "system.import": service.import_all,
        "system.wipe": service.wipe_all_data,
        "system.pause": service.set_paused,
        "settings.get": service.get_settings,
        "settings.update": service.update_settings,
        "settings.reset": service.reset_settings,
        "audio.devices": service.audio_devices,
        "audio.rescan": service.rescan_audio,
        "asr.models": service.asr_models,
        "asr.load": service.asr_load,
        "asr.unload": service.asr_unload,
        "asr.warm": service.asr_warm,
        "llm.status": service.llm_status,
        "llm.test": service.llm_test,
        "llm.warm": service.llm_warm,
        "llm.unload": service.llm_unload,
        "llm.pull": service.llm_pull,
        "llm.pull_cancel": service.llm_pull_cancel,
        "llm.delete": service.llm_delete,
        "llm.show": service.llm_show,
        "session.start": service.session_start,
        "session.stop": service.session_stop,
        "session.cancel": service.session_cancel,
        "session.inserted": service.mark_inserted,
        "process.text": service.process_text,
        "process.command": service.run_text_command,
        "vocabulary.list": service.vocabulary_list,
        "vocabulary.add": service.vocabulary_add,
        "vocabulary.update": service.vocabulary_update,
        "vocabulary.delete": service.vocabulary_delete,
        "vocabulary.export": service.vocabulary_export,
        "vocabulary.import": service.vocabulary_import,
        "corrections.list": service.corrections_list,
        "corrections.observe": service.corrections_observe,
        "corrections.delete": service.corrections_delete,
        "corrections.toggle": service.corrections_toggle,
        "corrections.clear": service.corrections_clear,
        "snippets.list": service.snippets_list,
        "snippets.add": service.snippets_add,
        "snippets.update": service.snippets_update,
        "snippets.delete": service.snippets_delete,
        "history.list": service.history_list,
        "history.get": service.history_get,
        "history.delete": service.history_delete,
        "history.clear": service.history_clear,
        "history.edit": service.history_edit,
        "history.stats": service.history_stats,
        "metrics.quality": service.quality_snapshot,
        "session.undo": service.record_undo,
        "profiles.list": service.profiles_list,
        "profiles.upsert": service.profiles_upsert,
        "profiles.delete": service.profiles_delete,
    }


# Methods that may block for more than a few milliseconds.
_SLOW = {
    "session.stop", "asr.load", "asr.warm", "asr.unload", "llm.test", "llm.warm",
    "llm.unload", "llm.pull", "llm.delete", "llm.show",
    "process.text", "process.command", "system.hardware", "metrics.quality",
    "system.info", "system.wipe", "system.import", "settings.update",
}


class RpcServer:
    def __init__(self, service: LocalFlowService, host: str = "127.0.0.1",
                 port: int = 0, token: str = ""):
        self.service = service
        self.host = "127.0.0.1" if host in ("", "0.0.0.0") else host
        self.port = port
        self.token = token or secrets.token_urlsafe(24)
        self.methods = _methods(service)
        self._clients: set[WebSocketServerProtocol] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server = None
        service.set_emitter(self.emit_threadsafe)

    # -- events ------------------------------------------------------------
    def emit_threadsafe(self, event: str, data: dict) -> None:
        """Called from any thread; hands the broadcast to the event loop."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            asyncio.run_coroutine_threadsafe(self._broadcast(event, data), loop)
        except RuntimeError:
            pass

    async def _broadcast(self, event: str, data: dict) -> None:
        if not self._clients:
            return
        message = json.dumps({"event": event, "data": data}, ensure_ascii=False, default=str)
        dead: list[WebSocketServerProtocol] = []
        for client in list(self._clients):
            try:
                await client.send(message)
            except Exception:
                dead.append(client)
        for client in dead:
            self._clients.discard(client)

    # -- connection handling ----------------------------------------------
    async def _handle(self, websocket: WebSocketServerProtocol) -> None:
        if not await self._authenticate(websocket):
            return
        self._clients.add(websocket)
        try:
            await websocket.send(json.dumps({"event": "ready", "data": {"ok": True}}))
            async for raw in websocket:
                await self._dispatch(websocket, raw)
        except websockets.ConnectionClosed:
            pass
        except Exception:
            log.exception("Connection handler failed")
        finally:
            self._clients.discard(websocket)

    async def _authenticate(self, websocket: WebSocketServerProtocol) -> bool:
        try:
            raw = await asyncio.wait_for(websocket.recv(), timeout=5.0)
            payload = json.loads(raw)
        except Exception:
            await websocket.close(code=4401, reason="auth required")
            return False
        if not isinstance(payload, dict) or not secrets.compare_digest(
            str(payload.get("token", "")), self.token
        ):
            await websocket.close(code=4401, reason="invalid token")
            log.warning("Rejected connection with bad token")
            return False
        return True

    async def _dispatch(self, websocket: WebSocketServerProtocol, raw: str | bytes) -> None:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        if len(raw) > MAX_MESSAGE_BYTES:
            await self._reply(websocket, None, error=RpcError("too_large", "Message too large."))
            return
        try:
            message = json.loads(raw)
        except ValueError:
            await self._reply(websocket, None, error=RpcError("bad_json", "Malformed message."))
            return
        if not isinstance(message, dict):
            await self._reply(websocket, None, error=RpcError("bad_request", "Expected an object."))
            return

        request_id = message.get("id")
        method_name = message.get("method")
        params = message.get("params") or {}
        if not isinstance(method_name, str) or not isinstance(params, dict):
            await self._reply(
                websocket, request_id, error=RpcError("bad_request", "Invalid method or params.")
            )
            return

        handler = self.methods.get(method_name)
        if handler is None:
            await self._reply(
                websocket, request_id,
                error=RpcError("unknown_method", f"Unknown method '{method_name}'."),
            )
            return

        params = self._filter_params(handler, params)
        try:
            if method_name in _SLOW:
                result = await asyncio.get_running_loop().run_in_executor(
                    None, functools.partial(handler, **params)
                )
            else:
                result = handler(**params)
                if inspect.isawaitable(result):
                    result = await result
        except TypeError as exc:
            await self._reply(websocket, request_id,
                              error=RpcError("bad_params", str(exc)))
            return
        except Exception as exc:
            code = getattr(exc, "code", "internal_error")
            detail = getattr(exc, "detail", "")
            log.warning("%s failed: %s", method_name, exc)
            await self._reply(websocket, request_id,
                              error=RpcError(code, str(exc) or "Request failed.", detail))
            return
        await self._reply(websocket, request_id, result=result)

    @staticmethod
    def _filter_params(handler: Callable[..., Any], params: dict) -> dict:
        """Drop unexpected keys so a malformed call cannot raise deep inside."""
        try:
            signature = inspect.signature(handler)
        except (TypeError, ValueError):
            return params
        if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values()):
            return params
        allowed = {
            name for name, p in signature.parameters.items()
            if p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
        }
        return {k: v for k, v in params.items() if k in allowed}

    async def _reply(self, websocket, request_id, result: Any = None,
                     error: RpcError | None = None) -> None:
        if error is not None:
            payload = {
                "id": request_id,
                "ok": False,
                "error": {"code": error.code, "message": str(error), "detail": error.detail},
            }
        else:
            payload = {"id": request_id, "ok": True, "result": result}
        try:
            await websocket.send(json.dumps(payload, ensure_ascii=False, default=str))
        except Exception:
            log.debug("Reply failed", exc_info=True)

    # -- lifecycle ---------------------------------------------------------
    async def serve(self) -> None:
        self._loop = asyncio.get_running_loop()
        async with websockets.serve(
            self._handle,
            self.host,
            self.port,
            max_size=MAX_MESSAGE_BYTES,
            ping_interval=20,
            ping_timeout=20,
            compression=None,
        ) as server:
            self._server = server
            sockets = getattr(server, "sockets", None) or []
            if sockets:
                self.port = sockets[0].getsockname()[1]
            self._announce()
            await asyncio.Future()

    def _announce(self) -> None:
        """Tell the parent process where to connect."""
        print(
            "LOCALFLOW_READY "
            + json.dumps({"port": self.port, "host": self.host, "token": self.token}),
            flush=True,
        )
        log.info("LocalFlow backend listening on %s:%s", self.host, self.port)

"""End-to-end test of the backend process and its JSON-RPC protocol.

Starts the real backend as a subprocess exactly the way the desktop shell does,
connects over the loopback WebSocket, and exercises the contract the Rust side
depends on. This is what catches a protocol change that unit tests would miss.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"

pytestmark = pytest.mark.skipif(
    not PYTHON.is_file(), reason="the development virtualenv is not present"
)


class BackendProcess:
    """Spawns the backend and speaks the same protocol Rust does."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.process: subprocess.Popen[str] | None = None
        self.port = 0
        self.token = ""
        self.websocket = None
        self._next_id = 1
        self.events: list[tuple[str, dict]] = []

    def start(self, timeout: float = 90.0) -> None:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(BACKEND)
        env["LOCALFLOW_DATA_DIR"] = str(self.data_dir)
        env["PYTHONIOENCODING"] = "utf-8"
        self.process = subprocess.Popen(
            [str(PYTHON), "-u", "-m", "localflow", "--port", "0", "--no-warm"],
            cwd=str(BACKEND),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
        )
        assert self.process.stdout is not None
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            line = self.process.stdout.readline()
            if not line:
                if self.process.poll() is not None:
                    raise RuntimeError("backend exited before becoming ready")
                continue
            if line.startswith("LOCALFLOW_READY "):
                info = json.loads(line[len("LOCALFLOW_READY ") :])
                self.port = info["port"]
                self.token = info["token"]
                assert info.get("host", "127.0.0.1") == "127.0.0.1"
                return
        raise TimeoutError("backend did not announce readiness")

    async def connect(self) -> None:
        """Open a fresh authenticated socket.

        pytest-asyncio gives each test its own event loop, and a websocket is
        bound to the loop that created it - so every test connects its own.
        """
        import websockets

        self.websocket = await websockets.connect(f"ws://127.0.0.1:{self.port}")
        await self.websocket.send(json.dumps({"token": self.token}))
        ready = json.loads(await asyncio.wait_for(self.websocket.recv(), timeout=10))
        assert ready.get("event") == "ready"

    async def disconnect(self) -> None:
        if self.websocket is not None:
            await self.websocket.close()
            self.websocket = None

    async def call(self, method: str, params: dict | None = None, timeout: float = 60.0):
        assert self.websocket is not None
        request_id = self._next_id
        self._next_id += 1
        await self.websocket.send(
            json.dumps({"id": request_id, "method": method, "params": params or {}})
        )
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            raw = await asyncio.wait_for(self.websocket.recv(), timeout=timeout)
            message = json.loads(raw)
            if "event" in message:
                self.events.append((message["event"], message.get("data", {})))
                continue
            if message.get("id") != request_id:
                continue
            if not message.get("ok"):
                raise AssertionError(f"{method} failed: {message.get('error')}")
            return message.get("result")
        raise TimeoutError(f"no reply to {method}")

    def close(self) -> None:
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()


@pytest.fixture(scope="module")
def data_dir():
    # ignore_cleanup_errors: Windows keeps the SQLite WAL handle open for a
    # moment after the backend exits, which is a teardown artefact, not a bug.
    with tempfile.TemporaryDirectory(
        prefix="localflow-test-", ignore_cleanup_errors=True
    ) as path:
        yield Path(path)


@pytest.fixture(scope="module")
def backend(data_dir):
    process = BackendProcess(data_dir)
    process.start()
    yield process
    process.close()


@pytest.fixture
async def session(backend: BackendProcess):
    """A connected backend for one test."""
    await backend.connect()
    yield backend
    await backend.disconnect()


@pytest.mark.asyncio
async def test_full_protocol(session: BackendProcess) -> None:
    backend = session

    # -- handshake and basics ------------------------------------------
    assert (await backend.call("system.ping"))["pong"] is True

    settings = await backend.call("settings.get")
    assert set(settings) >= {"audio", "asr", "llm", "processing", "hotkeys", "privacy"}
    assert settings["privacy"]["store_audio"] is False, "audio history must be off by default"
    assert settings["privacy"]["telemetry"] is False, "telemetry must be off by default"

    # -- settings round-trip -------------------------------------------
    updated = await backend.call(
        "settings.update", {"patch": {"processing": {"filler_aggressiveness": "aggressive"}}}
    )
    assert updated["processing"]["filler_aggressiveness"] == "aggressive"
    await backend.call(
        "settings.update", {"patch": {"processing": {"filler_aggressiveness": "balanced"}}}
    )

    # -- the processing pipeline ---------------------------------------
    result = await backend.call(
        "process.text",
        {
            "text": "um can you send Rahul the quotation tomorrow, actually no, Friday",
            "context": {"exe": "notepad.exe"},
            "force_llm": False,
        },
    )
    assert "Friday" in result["text"]
    assert "tomorrow" not in result["text"]
    assert "um" not in result["text"].lower().split()

    # -- vocabulary CRUD ------------------------------------------------
    added = await backend.call(
        "vocabulary.add", {"term": "Araxys", "sounds_like": ["Araxis", "a raxis"]}
    )
    assert added["item"]["term"] == "Araxys"
    repaired = await backend.call(
        "process.text",
        {"text": "please send this to Araxis today", "force_llm": False},
    )
    assert "Araxys" in repaired["text"], "vocabulary should repair the mishearing"
    await backend.call("vocabulary.delete", {"id": added["item"]["id"]})

    # -- snippets -------------------------------------------------------
    snippet = await backend.call(
        "snippets.add",
        {"name": "Test sig", "trigger": "test signature", "expansion": "Kind regards,\nKevin"},
    )
    expanded = await backend.call("process.text", {"text": "insert my test signature"})
    assert expanded["text"].startswith("Kind regards")
    assert expanded["snippet"] is not None
    await backend.call("snippets.delete", {"id": snippet["item"]["id"]})

    # -- voice commands are detected, not inserted ----------------------
    command = await backend.call("process.text", {"text": "undo that"})
    assert command["command"]["action"] == "undo"
    assert command["text"] == ""

    # -- history --------------------------------------------------------
    history = await backend.call("history.list", {"limit": 5})
    assert "items" in history and "stats" in history

    # -- hardware and catalogues ---------------------------------------
    hardware = await backend.call("system.hardware")
    assert "cuda_available" in hardware
    assert "recommendation" in hardware

    catalogues = await backend.call("system.catalogues")
    assert any(style["key"] == "professional" for style in catalogues["styles"])
    assert any(command["action"] == "undo" for command in catalogues["commands"])

    models = await backend.call("asr.models")
    assert any(model["key"] == "small" for model in models["catalog"])

    # -- privacy report -------------------------------------------------
    privacy = await backend.call("system.privacy")
    assert "Local" in privacy["audio_processing"]
    assert all(
        "127.0.0.1" in entry["endpoint"] or "huggingface" in entry["endpoint"]
        for entry in privacy["network"]
    )


@pytest.mark.asyncio
async def test_unknown_methods_are_rejected(session: BackendProcess) -> None:
    backend = session
    with pytest.raises(AssertionError, match="unknown_method"):
        await backend.call("os.system")
    with pytest.raises(AssertionError, match="unknown_method"):
        await backend.call("service.shutdown")


@pytest.mark.asyncio
async def test_bad_token_is_refused(backend: BackendProcess) -> None:
    import websockets

    async with websockets.connect(f"ws://127.0.0.1:{backend.port}") as socket:
        await socket.send(json.dumps({"token": "not-the-token"}))
        with pytest.raises(websockets.ConnectionClosed):
            await asyncio.wait_for(socket.recv(), timeout=10)


@pytest.mark.asyncio
async def test_malformed_input_does_not_crash_the_backend(session: BackendProcess) -> None:
    backend = session
    assert backend.websocket is not None
    for payload in ["not json at all", "[]", '{"method": 42}', '{"id": 1}']:
        await backend.websocket.send(payload)
    # The backend must still be answering afterwards.
    assert (await backend.call("system.ping"))["pong"] is True

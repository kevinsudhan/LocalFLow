# Development

```powershell
powershell -ExecutionPolicy Bypass -File scripts/setup.ps1     # once
powershell -ExecutionPolicy Bypass -File scripts/build.ps1 -Dev
```

`-Dev` starts Vite with hot reload and launches the Tauri shell against it. The
Python backend is spawned from `.venv` - edits to it need a restart, edits to
the React code do not.

## Layout

```
backend/localflow/
  __main__.py       entry point: logging, argument parsing, signal handling
  server.py         JSON-RPC over a loopback WebSocket; the method allowlist
  service.py        every operation the desktop shell can ask for
  session.py        one dictation: record, partials, transcribe
  audio/            device enumeration, capture with pre-roll ring buffer
  vad/              Silero via onnxruntime, plus an energy fallback
  asr/              faster-whisper engine, model catalogue, CUDA wiring
  processing/       the pipeline - one module per stage
  llm/              provider abstraction, Ollama client, the prompt
  context/          application classification, continuation analysis
  vocabulary/       fuzzy term repair and learned corrections
  snippets/  commands/  history/  database/  config/

desktop/src-tauri/src/
  main.rs           Tauri setup, tray, window wiring
  backend.rs        spawns Python, speaks JSON-RPC
  hotkey.rs         WH_KEYBOARD_LL push-to-talk
  winctx.rs         foreground window + UI Automation
  inject.rs         SendInput and clipboard insertion
  orchestrator.rs   the session state machine
  hud.rs  tray.rs  ipc.rs  autostart.rs

desktop/src/
  pages/            Hud, Dashboard, Settings, Models, History, Vocabulary,
                    Snippets, Privacy, Diagnostics, About, Onboarding
  components/ui/    the control set (neumorphic primitives live in styles/)
  services/bridge.ts  the only place that calls invoke()
  stores/appStore.ts  zustand state
```

## Working on the processing pipeline

This is where most of the product lives, and it is the easiest part to work on
because it needs neither audio nor a GPU.

```powershell
$env:PYTHONPATH="backend"
.\.venv\Scripts\python.exe -m pytest tests/unit -q

# Try a single utterance
.\.venv\Scripts\python.exe -c @"
import sys; sys.path.insert(0,'backend')
from localflow.processing.backtrack import resolve_corrections
print(resolve_corrections('Ship it Monday, actually Tuesday.').text)
"@

# Or the whole pipeline, fast
.\.venv\Scripts\python.exe benchmarks/run_benchmark.py --no-audio --no-llm -v
```

There is also a sandbox in the app: Diagnostics › Pipeline sandbox runs text
through the real pipeline with a toggle for the language model.

**Adding a behaviour starts with a benchmark case.** Add it to
`benchmarks/cases.json` with `must_contain` / `must_not_contain`, watch it fail,
then make it pass. That keeps the zero-edit rate meaningful and stops a new rule
from quietly breaking an old one.

## Working on the Rust side

```powershell
cd desktop/src-tauri
cargo test
cargo clippy --all-targets
```

Anything touching Win32 is `#[cfg(windows)]` with a stub for other platforms, so
the crate still type-checks elsewhere.

Two things to know:

- **The keyboard hook callback must stay cheap.** It runs on the input path for
  every key the user presses. Compare, swallow, post to a channel - nothing else.
- **UI Automation calls can block forever** if the target application
  misbehaves. Everything goes through `ContextCollector` with a timeout; never
  call UIA from the session thread.

## The protocol

Rust and Python speak JSON-RPC over a loopback WebSocket:

```json
→ {"id": 7, "method": "process.text", "params": {"text": "hello"}}
← {"id": 7, "ok": true, "result": {"text": "Hello."}}
← {"event": "audio.level", "data": {"level": 0.12}}
```

Adding a method means touching three allowlists - deliberately:

1. `backend/localflow/server.py` - `_methods()`, and `_SLOW` if it can block
2. `desktop/src-tauri/src/ipc.rs` - `ALLOWED`
3. `desktop/src/services/bridge.ts` - the typed wrapper

Test it in `tests/integration/test_backend_protocol.py`, which drives the real
process.

## Running the backend on its own

Useful when the shell is in the way:

```powershell
$env:PYTHONPATH="backend"; $env:LOCALFLOW_DATA_DIR="$PWD\.devdata"
.\.venv\Scripts\python.exe -m localflow --port 8799 --token dev --log-level DEBUG
```

It prints `LOCALFLOW_READY {"port": 8799, ...}` and then serves. Point a
WebSocket client at it, or set `LOCALFLOW_PYTHON` and let the shell attach.

## Environment variables

| Variable | Effect |
|---|---|
| `LOCALFLOW_DATA_DIR` | Use a different data folder - essential for testing |
| `LOCALFLOW_PYTHON` | Interpreter the shell should spawn |
| `LOCALFLOW_BACKEND_DIR` | Where `localflow/` lives |
| `LOCALFLOW_LOG` | Rust log filter, e.g. `debug` |
| `LOCALFLOW_ENABLE_XET` | Re-enable Hugging Face's Xet downloader (off by default; it stalls on some Windows networks) |

## Design rules worth keeping

**Deterministic first.** Any new behaviour should be attempted without the
language model. The model is for genuine ambiguity, and every millisecond it
costs is paid on the user's cursor.

**Never trust the model's output.** Anything it returns goes through
`validate_llm_output`. If a new rule lets the model change something, add a
check that the change is legitimate.

**Never guess destructively.** The self-correction engine only removes text when
it can name what is being replaced. Prefer leaving speech alone over deleting
the wrong thing.

**Errors are sentences, not exceptions.** "Ollama isn't running. Start Ollama
and try again." - not a stack trace. Technical detail belongs in the `detail`
field, shown under *Details*.

## Releasing

```powershell
powershell -File scripts/build.ps1 -Test
powershell -File scripts/build.ps1 -Bench
powershell -File scripts/build.ps1 -Installer
```

The installer bundles a standalone Python runtime (`scripts/bundle_runtime.py`)
so the installed application does not need a system Python. Output lands in
`desktop/src-tauri/target/release/bundle/nsis/`.

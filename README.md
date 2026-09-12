# LocalFlow

Voice as a first-class input method for Windows.

Hold a key in any application, speak, let go - polished text appears at your
cursor. Speech recognition and AI cleanup run entirely on your own machine.
There is no account, no server, and no telemetry.

```
        HOLD Ctrl+Space          ●●●●●●●●●          RELEASE
             │                   listening              │
             ▼                                          ▼
    "uh can you send Rahul the      →    Can you send Rahul the quotation
     quotation tomorrow, actually         on Friday, and tell him customs
     no, Friday, and tell him             has delayed the shipment.
     customs has delayed the
     shipment"
```

---

## What it does

| | |
|---|---|
| **Works everywhere** | Notepad, Chrome, Gmail, Slack, WhatsApp, Word, Google Docs, VS Code, Cursor, terminals, ChatGPT - anywhere you can type |
| **Removes disfluency** | "uh", "um", and discourse fillers, without deleting words that carry meaning |
| **Resolves corrections** | "Send it Monday… actually Tuesday" → "Send it Tuesday." The superseded value disappears |
| **Knows where it is** | Professional prose in Outlook, casual in Slack, verbatim in a terminal, identifier-safe in VS Code |
| **Learns your words** | Names, products and jargon you add - plus spellings it picks up from your own edits |
| **Never leaves the PC** | Audio stays in memory, transcription is local, the AI model is local |

---

## What it looks like

![The LocalFlow dashboard](docs/images/dashboard-dark.png)

The same window in the light theme:

![The dashboard in the light theme](docs/images/dashboard-light.png)

First run checks each thing it needs and reports what it found. The
microphone step names the device that is actually open, gives you a sentence
to read, and says so plainly when a device is delivering silence:

![The microphone check](docs/images/onboarding-microphone.png)

The shortcut is a system-wide keyboard hook, so when it fails there is
nothing to see anywhere. This screen makes it visible, and if no key arrives
it reports what the hook is actually observing:

![The shortcut tester](docs/images/onboarding-shortcut.png)

A full tour of every screen is in [INTERFACE.md](docs/INTERFACE.md).

---
Measured on the reference machine (RTX 3060 Laptop, 6 GB): **100 % zero-edit
rate** across 40 processing cases, median **0.9 ms** for the deterministic path
and ~**700 ms** end-to-end for a spoken sentence. See
[TESTING.md](docs/TESTING.md) for the methodology and the caveats.

---

## Requirements

| | Minimum | Recommended |
|---|---|---|
| OS | Windows 10 1809+ | Windows 11 |
| RAM | 8 GB | 16 GB |
| GPU | none (CPU works) | NVIDIA with 4 GB+ VRAM |
| Disk | 3 GB | 6 GB |

Needed to build from source:

- **Python 3.11** (3.10-3.13 work) - [python.org](https://www.python.org/downloads/)
- **Node.js 18+** - [nodejs.org](https://nodejs.org/)
- **Rust** - [rustup.rs](https://rustup.rs/)
- **Visual Studio Build Tools** with the *Desktop development with C++* workload -
  Tauri needs the MSVC linker
- **Ollama** (optional) - [ollama.com](https://ollama.com). Without it LocalFlow
  still works; only the AI cleanup step is unavailable.

---

## Getting started

```powershell
git clone https://github.com/kevinsudhan/LocalFLow.git localflow
cd localflow
powershell -ExecutionPolicy Bypass -File scripts/setup.ps1
powershell -ExecutionPolicy Bypass -File scripts/build.ps1 -Dev
```

`setup.ps1` creates the Python environment, installs the CUDA runtime wheels if
you have an NVIDIA GPU, downloads the voice-activity model, installs the
frontend packages and generates the icons. It is safe to re-run.

On first launch LocalFlow walks you through the microphone check, picks a speech
model that fits your GPU, offers to download a language model and lets you set
your shortcut. The speech model downloads once (~500 MB-1.6 GB depending on the
model) and then works offline forever.

### Building an installer

```powershell
powershell -File scripts/build.ps1 -Installer
```

This bundles a standalone Python runtime so the installed application does not
depend on a system Python. The output lands in
`desktop/src-tauri/target/release/bundle/nsis/`.

---

## Using it

**Hold `Ctrl+Space`**, speak, release. That is the whole interface.

A quick *tap* instead of a hold locks recording on so you can put the keyboard
down; tap again to finish. `Escape` cancels. `Ctrl+Shift+Z` removes the text
LocalFlow just inserted.

### Spoken commands

Recognised only when the entire dictation is the command, so they never fire
mid-sentence.

| Say | Result |
|---|---|
| "undo that" | Removes the last insertion |
| "delete last sentence" | Deletes the final sentence of it |
| "new paragraph" / "new line" | Inserts a break |
| "make that formal / casual / concise" | Restyles what was just inserted |
| "translate this to Tamil" | Translates it (allowlisted languages only) |
| "insert my email signature" | Expands a snippet |

### Dictating punctuation

You do not need to. Say "Hello John, how are you?" normally and the punctuation
is inferred. If you *want* explicit control, "comma", "period", "question mark",
"new paragraph" and friends all work - and LocalFlow still writes the word when
you are clearly talking *about* it ("the comma is missing").

---

## How it works

```
  Ctrl+Space ──► Rust: low-level keyboard hook
                   │
                   ├──► UI Automation ──► app, caret, selection, surrounding text
                   │                      (runs while you are still speaking)
                   │
                   └──► Python ──► microphone (pre-roll buffer)
                                     │
                                     ├─ Silero VAD      trim silence, keep syllables
                                     ├─ faster-whisper  transcribe (GPU)
                                     │
                                     ├─ normalise ─ vocabulary ─ self-correction
                                     ├─ fillers ─ lists ─ punctuation ─ paragraphs
                                     │
                                     ├─ gate: does this need reasoning?
                                     │    no  ──────────────────────────┐
                                     │    yes ─► Ollama ─► validate ────┤
                                     │                                  │
                   ┌──────────────────────────────────────────────────┘
                   ▼
        Rust: SendInput / clipboard paste ──► your application
```

Two design decisions do most of the work:

**Deterministic first.** The local model is only called when a specific signal
says reasoning is needed - an unresolved correction, an ambiguous filler, a
run-on sentence, low recogniser confidence, or a non-neutral style. In the
benchmark that is 12 % of dictations; the other 88 % finish in under a
millisecond of processing.

**Nothing the model returns is trusted.** Every rewrite is checked against the
input: numbers, URLs, acronyms and your vocabulary must survive, a question must
stay a question, and no new content words may appear. A rewrite that fails is
discarded and the deterministic result is used instead. That is what makes it
safe to run a general-purpose chat model as a copy editor.

Full detail in [ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## Documentation

| | |
|---|---|
| [INTERFACE.md](docs/INTERFACE.md) | A tour of every screen, with screenshots |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | How the pieces fit and why |
| [DEVELOPMENT.md](docs/DEVELOPMENT.md) | Working on the code |
| [MODEL_SETUP.md](docs/MODEL_SETUP.md) | Choosing, downloading and sizing models |
| [PRIVACY.md](docs/PRIVACY.md) | Exactly what is stored and what touches the network |
| [TESTING.md](docs/TESTING.md) | The test suite and the quality benchmark |
| [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | When something does not work |

---

## Project layout

```
backend/localflow/     Python: audio, VAD, ASR, processing pipeline, LLM, SQLite
desktop/src/           React: HUD, settings, onboarding, diagnostics
desktop/src-tauri/     Rust: hotkeys, tray, UI Automation, text insertion
benchmarks/            Dictation quality benchmark and model comparison
tests/                 Unit and integration tests, spoken audio fixtures
scripts/               Setup, build, icon and fixture generation
```

---

## Licence and credits

Built on [faster-whisper](https://github.com/SYSTRAN/faster-whisper) and
[CTranslate2](https://github.com/OpenNMT/CTranslate2), [Silero
VAD](https://github.com/snakers4/silero-vad), [Ollama](https://ollama.com),
[Tauri](https://tauri.app) and React. Each remains under its own licence.

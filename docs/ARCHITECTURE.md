# Architecture

LocalFlow is three processes that each do what they are best at.

```
┌─────────────────────────────────────────────────────────────────────┐
│ localflow.exe  (Rust / Tauri)                                       │
│                                                                     │
│  keyboard hook · system tray · HUD window · settings window         │
│  UI Automation · text insertion · clipboard · autostart             │
└──────────────┬──────────────────────────────────┬───────────────────┘
               │ Tauri IPC                        │ JSON-RPC over
               │                                  │ 127.0.0.1 WebSocket
┌──────────────▼───────────────┐   ┌──────────────▼───────────────────┐
│ WebView2  (React)            │   │ python -m localflow              │
│                              │   │                                  │
│  HUD overlay · settings      │   │  audio · VAD · Whisper           │
│  onboarding · diagnostics    │   │  processing · Ollama · SQLite    │
└──────────────────────────────┘   └──────────────────────────────────┘
```

## Why this split

**Rust owns the operating system.** Global keyboard hooks, UI Automation,
`SendInput` and the tray are all Win32 work that needs to be fast, in-process
and never blocked. Doing them from Python would mean a `ctypes` layer around
every call and a GIL in the path of a keyboard hook.

**Python owns the machine learning.** faster-whisper, Silero and the Ollama
client all live in Python, and the processing pipeline is text manipulation -
exactly the code that is cheapest to write, test and change in Python. Running
it out-of-process also means a crash in a model takes down a restartable child,
not the application.

**The webview owns nothing.** It renders and it sends commands. It never learns
the backend's port or token; every call goes through a Rust command that
enforces its own allowlist.

## The dictation path

Numbers are medians from the reference machine (RTX 3060 6 GB,
`large-v3-turbo` + `qwen2.5:7b-instruct`).

| Step | Where | Cost |
|---|---|---|
| Hotkey down detected | Rust hook thread | < 1 ms |
| Recording starts (pre-roll already buffered) | Python | 0 ms |
| Context captured - app, caret, selection, surrounding text | Rust UIA thread | 20-450 ms, **concurrent** |
| Partial transcripts while speaking | Python | every 750 ms |
| Hotkey up → audio finalised (+ tail buffer) | Python | 350 ms |
| Silero VAD trims silence | Python | 1-17 ms |
| Whisper transcribes | Python, GPU | ~660 ms |
| Deterministic processing | Python | ~1 ms |
| Local model, **only if the gate fires** | Ollama | ~1600 ms (12 % of the time) |
| Validation | Python | < 1 ms |
| Text inserted | Rust | 5-60 ms |

Two things keep this fast:

- **Context collection overlaps recording.** The UIA query runs on its own COM
  thread while the user is still speaking, so its cost is hidden entirely.
- **The pre-roll ring buffer.** The microphone stream stays open while LocalFlow
  is armed, holding the last 500 ms in a ring that is continuously overwritten.
  Pressing the hotkey starts *retaining* audio that is already in flight, rather
  than opening a device - which on Windows takes 50-150 ms, exactly the length
  of a first syllable.

## The processing pipeline

`backend/localflow/processing/pipeline.py`

```
raw ASR
  │
  ├─ normalise        unicode, spacing, stutters, spoken punctuation
  │                   (URLs/paths/identifiers masked throughout)
  ├─ commands         whole-utterance match → bypass everything
  ├─ snippets         trigger match → insert verbatim, bypass everything
  ├─ vocabulary       your terms + learned corrections, fuzzy-matched
  ├─ self-correction  "actually Tuesday" → the superseded value disappears
  ├─ fillers          hesitations always; discourse markers only when provable
  ├─ lists            explicit enumerations only
  ├─ punctuation      terminate sentences, infer questions, fix casing
  ├─ paragraphs       from the pauses in the audio, not guessed from text
  │
  ├─ GATE ───────────► deterministic output is enough (88 %)
  │                    └─────────────────────────────────┐
  └─ local model ──► validate ──► accept or discard ─────┤
                                                         ▼
                                                    final text
```

### The gate

`ProcessingPipeline.should_use_llm` escalates only on a concrete signal:

| Signal | Why |
|---|---|
| An unresolved correction cue | The deterministic stage found "actually" but could not name what it replaced |
| Ambiguous fillers | "basically" at a position where it might carry meaning |
| Filler density > 5 % | Heavily disfluent speech |
| A non-neutral style | Professional, casual, concise and developer are rewrites by definition |
| Recogniser confidence below −0.75 | Whisper was unsure; the text likely needs repair |
| A 32-word sentence with no commas | A run-on that needs splitting |
| A disfluent restart | "I was - I mean we were" |
| Email with 12+ words | Formatting is expected there |

It refuses outright for terminals, password fields, apps the user disabled it
for, and anything under three words.

### Self-correction

The hard part is not detecting "actually" - it is knowing what it replaces. The
rule that makes this usable:

> A correction is only applied when a concrete replacement target is found.

Given "Ship it Monday, actually Tuesday", the engine classifies "Tuesday" as a
weekday, looks backwards for a weekday, finds "Monday", and replaces it. Given
"The plan is fine, actually let me think about it", there is no target of a
matching type, so **nothing is changed** and the cue is reported as unresolved -
which is one of the signals that escalates to the model.

That turns "is this a correction?" into the much safer "can I name what it
replaces?".

### Protected literals

Sentence-casing and spacing repair assume prose. Applied to
`https://example.com/api/v2` they produce `https: //example. Com/api/v2`. So
`processing/protect.py` swaps URLs, file paths, `snake_case`, `camelCase`,
package names, version strings and command flags for opaque placeholders before
the prose stages run, and restores them afterwards. The same literals are then
handed to the validator, which requires them to survive the model too.

### Validation

`processing/validate.py` rejects a model rewrite that:

- is empty, or more than 2.2× / less than 0.35× the input length
- opens like an answer ("Sure,", "I'll send…") or describes its own edits
- drops a number, URL, email, path, acronym or vocabulary term
- invents a number that was not spoken (list numbering and
  "five" → "5" excepted)
- introduces a content word absent from the input - the check that catches a
  model "helpfully" turning "running five minutes late" into "Arrived a bit
  early, but running five minutes late"
- turns a dictated question into a statement

A rejection is not an error. The deterministic result is inserted and the
reason is recorded in diagnostics.

## Windows integration

### Push to talk

`RegisterHotKey` reports a press, never a hold. Push-to-talk needs key-down and
key-up, needs to work under any foreground application, and needs to swallow the
chord so `Ctrl+Space` does not also reach the editor underneath. `WH_KEYBOARD_LL`
is the only mechanism that gives all three.

The hook runs on a dedicated thread with its own message pump and does the
minimum: compare, swallow, post to a channel. Events flagged `LLKHF_INJECTED`
are ignored, so LocalFlow's own `SendInput` cannot feed itself.

### Reading the caret

There is no Windows API that returns "the text around the caret" for every
application, so `winctx.rs` tries three tiers:

1. **UIA TextPattern** - real text before and after the caret plus the current
   selection. Works in Word, Notepad, most Win32 edit controls and
   Chromium/Electron surfaces.
2. **UIA ValuePattern** - the whole field value. Enough to know the field is
   empty; not enough to locate the caret.
3. **Nothing** - `has_uia_text: false`, and the dictation is treated as starting
   a fresh sentence, which is the safe default.

All of it happens on one COM thread that keeps the `IUIAutomation` object alive,
behind a 450 ms timeout - a misbehaving application must never hang a dictation.

Browser URLs are deliberately *not* read via UIA. Walking a Chromium tree for
the address bar costs 50-200 ms and breaks whenever the browser changes; the
window title already contains "Gmail", "Google Docs" or "ChatGPT", which is what
the classifier needs.

### Inserting text

Two mechanisms, because neither wins everywhere:

- **Unicode `SendInput`** types the text as synthetic key events. Never touches
  the clipboard, works in consoles, slow for long text.
- **Clipboard paste** is one `Ctrl+V`: fast, reliable in Electron apps, and it
  gives the host a *single undo unit*, which is what makes `Ctrl+Z` behave
  naturally afterwards.

Automatic mode types under 120 characters and pastes above it, with per-app
overrides (terminals type, Slack and Discord paste).

The clipboard is only borrowed when its contents can be restored exactly. If
you have an image or a file on it, LocalFlow types instead of destroying it.

One subtlety worth naming: the user is often still holding `Ctrl` when
insertion begins. Typing Unicode with `Ctrl` down turns every character into a
shortcut, so `inject.rs` waits up to 300 ms for the physical modifiers to come
up and then synthesises key-up for any that are still held.

## Data

SQLite at `%APPDATA%\LocalFlow\localflow.db`, WAL mode, one shared connection
behind a re-entrant lock - dictation traffic is a few rows per utterance, so a
pool would be pure overhead.

`settings` · `vocabulary` · `learned_corrections` · `snippets` · `history`
(+ FTS5 index) · `application_profiles`

Audio is never stored in the database. With audio history enabled (off by
default) WAV files live in `%APPDATA%\LocalFlow\audio\` and are referenced by
path.

## Security

- The backend binds `127.0.0.1` on an ephemeral port, with a random token per
  launch. Rust refuses to connect to any non-loopback host.
- Both the backend and the Rust command layer use explicit method allowlists.
  There is no dynamic dispatch to arbitrary attributes.
- Voice commands are a fixed allowlist mapping to named actions. No dictated
  text ever reaches a shell.
- Ollama model names are validated against a charset before being sent.
- Password fields are detected via UIA; their contents are never read and the
  language model is disabled for them.

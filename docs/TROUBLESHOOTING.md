# Troubleshooting

Start with **Settings › Advanced › Developer mode**, then the **Diagnostics**
page. It shows what each stage is doing and where the last dictation spent its
time. Logs are at `%APPDATA%\LocalFlow\logs\` (Help › Open log folder in the
tray menu).

---

## The log files

Two logs, both under `%APPDATA%\LocalFlow\logs\`:

| File | Written by | Contains |
|---|---|---|
| `backend.log` | Python | Model loading, microphone open, VAD backend, transcription timings |
| `desktop.log` | Rust | Keyboard hook, backend supervision, hotkey events, text insertion |

`desktop.log` is the one people forget. The shell is a GUI binary with no
console, so anything it writes to standard error is discarded; the file is the
only record that the hook fired or that the text took the clipboard path.

Useful lines to search for:

```
Keyboard hook installed on thread    the shortcut is armed
hotkey event: Down                   a real key press arrived
Microphone open: [21] ...            which device is actually recording
Whisper ready: large-v3-turbo        which model loaded, on which device
inserted via clipboard (62 chars)    which insertion path the text took
```

---

## The microphone meter never moves

**Check which device is actually open.** The `Listening on` line on the
microphone screen, and `Microphone open:` in `backend.log`, name the device that
was really opened, which is not always the one that is configured. A device that
fails to open falls back to another.

If that line names `Primary Sound Capture Driver` or `Microsoft Sound Mapper`,
LocalFlow is recording from a Windows router rather than a microphone. Those
always open successfully and can deliver silence indefinitely. Pick a named
device in `Settings > Audio`.

**Rescan after plugging anything in.** PortAudio reads the device list once when
it initialises and never updates it, so a headset connected after LocalFlow
started is invisible, and the cached device indices can point at endpoints
Windows has since renumbered. The `Rescan devices` button re-enumerates and
reopens the stream. The `Rescan` button in `Settings > Audio` does the same.

**A jack that reports Active can still be silent.** Windows marks a combo jack
as plugged in based on impedance detection, which a headphone-only plug also
triggers. If the meter reads a flat 0.00 percent with a device open and
streaming, the endpoint is delivering digital silence: check the Windows volume
mixer and that the microphone is not muted at the device level.

---

## Dictation is suddenly very slow, or appears to hang

Check free VRAM on the dashboard first.

Whisper and the Ollama cleanup model are both resident on the GPU, and on a
6 GB card they can together exceed it. Past roughly 90 percent occupancy Windows
begins paging GPU memory over PCIe, and Whisper inference does not degrade
gracefully. Measured on an RTX 3060 Laptop with `large-v3-turbo` loaded and a 3B
language model also resident, the same one second clip took:

| GPU occupancy | Transcription time |
|---|---|
| 5797 of 6144 MB (94 percent) | 345 seconds |
| 3387 of 6144 MB (55 percent) | 0.43 seconds |

That is roughly 800 times slower, and it looks exactly like a hang.

The default `keep_alive` for the language model is deliberately short for this
reason: it runs on a minority of dictations and must not squat on memory the
transcriber needs on every one. If you still see this:

- Lower `keep_alive` further in `Settings > AI`.
- Choose a smaller cleanup model, or turn cleanup off entirely. The
  deterministic pipeline handles most dictations without it.
- Choose a smaller speech model. This costs accuracy on every dictation, so try
  the other two first.

---

## A dictated list stays on one line

LocalFlow formats a spoken list as bullets when it is confident the utterance is
an enumeration. It needs two things:

1. An announcement: a list noun such as `things`, `items` or `the following`, or
   a number followed by a plural noun such as `five AIs`.
2. A closed series of at least three short items, ending with `and` or `or`.

If you state a count it must match the number of items. Saying "five things" and
then listing four leaves the text as a sentence, on the assumption that the
commas are doing something other than separating list items.

These all produce the same list, because the punctuation Whisper invents for a
pause varies and cannot be relied on:

```
I need five things: apple, banana and mango.
I need five things. It will be apple, banana and mango.
I need five things which are apple, banana and mango.
I need five things, apple, banana and mango.
```

Ordinary prose is deliberately left alone. "I went to the shop, bought some milk
and came home" stays a sentence.

---

## Text arrives garbled, with characters missing or repeated

This is the typed insertion path failing, and it looks like `I y 5 things,
rrrrrot` rather than a clean error.

LocalFlow pastes by default because a paste is one atomic operation, while
typing synthesises a key event per character and races the target application's
input queue. Check `desktop.log`:

```
inserted via clipboard (62 chars, 27 ms)     good
inserted via unicode (68 chars, 69 ms)       typed, and at risk
```

If it is typing when you did not ask it to, the line above usually explains why:

```
Clipboard insertion failed (...); typing instead
```

Terminals are pinned to typed input on purpose, because they cannot accept a
`Ctrl+V`. For anything else, set `Settings > Insertion > Method` to `Clipboard`.

Multi-line text is always pasted regardless of the setting, because synthesising
a newline means pressing Return, and in a chat box or a search field Return is
the submit button.

---

## A model downloads but then fails to load

Look for `A required privilege is not held by the client` in `backend.log`.

Hugging Face stores a downloaded model once in a blob directory and links it
into place. Windows refuses to create symbolic links without administrator
rights or Developer Mode, so the download completes and the link never happens,
leaving a model directory that is missing files such as `config.json`.

LocalFlow detects this specific failure and retries with file copies, which
costs disk space but always works. If you would rather have the links, enable
Developer Mode in Windows Settings under `Privacy and security > For
developers`.

---

## The shortcut does nothing

**Another application already owns it.** `Ctrl+Space` is used by IME switchers,
Visual Studio IntelliSense and some launchers. Whoever registered it first wins.
Pick a different shortcut in Settings › Keyboard - `Alt+D`, `F9` and
`Ctrl+Shift+Space` are rarely taken.

**The focused application runs as administrator.** Windows blocks a
normal-privilege process from sending input to an elevated one - User Interface
Privilege Isolation. Either run that application normally, or run LocalFlow as
administrator too.

**The keyboard hook was refused.** Some endpoint-security products block
`WH_KEYBOARD_LL`. Check the log for `Windows refused the keyboard hook`. You
will need an exception for `localflow.exe`.

**LocalFlow is paused.** The tray icon shows it, and the sidebar toggle resumes.

---

## Nothing is transcribed

**Watch the level meter** in Settings › Audio while you speak. If it does not
move, the problem is before speech recognition.

- Wrong device selected - pick the right one from the dropdown.
- Windows microphone permission - Settings › Privacy & security › Microphone,
  and make sure *Let desktop apps access your microphone* is on.
- Another application holds the microphone exclusively - close it.
- Muted in hardware - check the physical switch or mute key.

If the meter moves but you get "No speech detected", the voice-activity detector
is discarding your audio. Lower **Sensitivity** in Settings › Speech, or turn
VAD off to confirm that is the cause.

---

## The first word keeps getting cut off

Raise **Pre-roll buffer** (Settings › Audio) to 700-1000 ms. It keeps audio from
just *before* you pressed the key.

If the *last* word is clipped instead, raise **Tail buffer**.

---

## It is slow

Check Diagnostics for the per-stage breakdown; the fix depends on which stage
dominates.

| Slow stage | Fix |
|---|---|
| Transcription | You are probably on CPU. Diagnostics shows the device. See "CUDA is not being used" below, or choose a smaller model. |
| Local model | Expected if it partly offloads to CPU. Switch to `qwen2.5:3b-instruct`, or turn AI cleanup off. |
| Everything, first dictation only | The model is loading. Turn on **Keep the model warm** in Settings › Speech. |

Remember the language model only runs on about one dictation in eight. If
*every* dictation is slow, it is not the language model.

---

## CUDA is not being used

Diagnostics › Hardware shows `GPU: none` or the speech engine shows `Device:
CPU`.

1. Confirm the driver works: `nvidia-smi` in a terminal.
2. Reinstall the CUDA runtime wheels:
   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r backend/requirements-gpu.txt
   ```
3. Verify LocalFlow can see them:
   ```powershell
   .\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'backend'); from localflow.asr.gpu import probe; print(probe())"
   ```
   `cuda_dll_dirs` should list three paths under `nvidia/`.

CTranslate2 needs **cuDNN 9** and **cuBLAS for CUDA 12**. A full CUDA Toolkit
install is not required and does not help.

If the GPU genuinely will not work, LocalFlow falls back to CPU automatically
and says so - it does not fail.

---

## A model download hangs at 0 %

Hugging Face's Xet transfer backend stalls on some Windows networks. LocalFlow
disables it by default; if you re-enabled it, unset `LOCALFLOW_ENABLE_XET` and
delete the partial download:

```powershell
Remove-Item -Recurse "$env:APPDATA\LocalFlow\models\whisper\models--*"
```

An Ollama pull that hangs is Ollama's own download - check that `ollama serve`
is healthy.

---

## "Ollama isn't running"

Start it (it usually runs as a background service), then click **Retry** on the
Models page. Verify independently:

```powershell
curl http://127.0.0.1:11434/api/tags
```

If Ollama is on a non-default port, set the address in Settings › AI. LocalFlow
only accepts loopback addresses.

**You do not need Ollama.** With AI cleanup off, LocalFlow still removes
fillers, fixes punctuation, resolves spoken corrections and formats lists. The
benchmark scores 40/40 with the model disabled.

---

## The text goes to the wrong place, or nowhere

**The HUD is not stealing focus** - it is created non-activating and
click-through. If insertion fails, it is the target application.

- Elevated target - see the first section.
- Electron apps dropping synthetic keystrokes - set Settings › Insertion ›
  Method to **Paste**, or raise **Typing speed** to 3-5 ms.
- Terminals - these default to typing, because pasting behaves differently
  across terminal emulators. If your terminal handles `Ctrl+V`, Paste is faster.

---

## My clipboard was replaced

It should not be. LocalFlow saves and restores it, and when the clipboard holds
something it *cannot* restore - an image, a file, rich content - it types the
text instead of pasting.

If you see this, please check Settings › Insertion › **Restore my clipboard** is
on, and note what was on the clipboard at the time.

---

## Ctrl+Z does not undo cleanly

Pasted text is a single undo unit, so `Ctrl+Z` removes it in one step. *Typed*
text may be several units in some applications - that is the application's
behaviour, not LocalFlow's.

Use `Ctrl+Shift+Z` (LocalFlow's own undo) or say *"undo that"* for an exact
removal, or set Insertion › Method to **Paste**.

---

## Filler words are not being removed

The deterministic stage is deliberately conservative: it removes "uh" and "um"
always, but only removes "basically", "actually" or "like" when it can prove
they carry no meaning in that sentence. Ambiguous cases are passed to the
language model.

Raise Settings › Formatting › **How aggressively** to *Strong*, or turn on AI
cleanup.

---

## A correction was not resolved

LocalFlow only applies a spoken correction when it can identify what is being
replaced. "Ship it Monday, actually Tuesday" works because Tuesday is a weekday
and so is Monday. "The plan is fine, actually let me reconsider" has no matching
target, so the text is left exactly as spoken and escalated to the language
model - which needs AI cleanup to be on.

This is intentional. Guessing would sometimes delete the wrong thing.

---

## My name or jargon is always misspelled

Add it in **Vocabulary**, with what the recogniser actually produces in the
*What it sounds like* field. That both biases Whisper towards the term and
repairs near-misses afterwards.

Or just fix it once in History - after the third time, LocalFlow learns it.

---

## Starting fresh

```powershell
# Settings only (vocabulary, snippets and history are kept)
#   Settings › Advanced › Reset settings

# Dictation data only
#   Privacy › Delete my data

# Everything, including downloaded models
Remove-Item -Recurse "$env:APPDATA\LocalFlow"
```

---

## Filing a useful report

Include Diagnostics (the three cards at the top), the relevant tail of
`%APPDATA%\LocalFlow\logs\backend.log`, the application you were dictating into,
and what you said versus what you got.

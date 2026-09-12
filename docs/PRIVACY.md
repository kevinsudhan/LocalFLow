# Privacy

LocalFlow has no account, no server and no analytics. This document states
exactly what happens to your voice and your text, and what can touch the
network.

## What happens to your voice

1. The microphone stream is held open while LocalFlow is armed, so that pressing
   the hotkey does not clip your first syllable. Audio flows into a **ring
   buffer of the last 500 ms that is continuously overwritten** and never
   written anywhere.
2. Pressing the hotkey starts *retaining* audio in memory.
3. Releasing it hands that NumPy array to Silero VAD and then to faster-whisper,
   both running in the LocalFlow process on your machine.
4. The array is discarded when the dictation finishes.

Audio is **never** written to disk unless you explicitly turn on audio history
(Settings › History), which is off by default.

## What is stored, and where

Everything lives in one folder: `%APPDATA%\LocalFlow\`.

| Path | Contents | Default |
|---|---|---|
| `localflow.db` | Settings, vocabulary, snippets, learned corrections, dictation history | History on, 90-day retention |
| `audio/` | WAV recordings, one per dictation | **Off** |
| `models/` | Downloaded Whisper and VAD weights | - |
| `logs/` | Rotating application logs (no transcript text) | - |
| `runtime/` | Bundled Python interpreter, installer builds only | - |

Deleting that folder removes every trace of LocalFlow's data.

### Dictation history

With history on, each dictation stores: timestamp, application name and window
title, the raw transcript, the final text, detected language, duration and
per-stage latency. You can search, edit, copy, re-insert and delete entries, or
clear everything, from the History page. Settings › History turns it off
entirely, and there is a retention setting.

### Audio history

Off by default, and the interface says why: turning it on writes a recording of
your voice for every dictation. The files stay on your PC, but they are
recordings of your voice - the feature exists so you can check what LocalFlow
actually heard, not as a default.

### Learned corrections

When you edit a dictation in History, LocalFlow compares your edit to what it
produced and records single-word substitutions. After three occurrences of the
same pair it starts applying that spelling automatically. You can view, disable
and delete every learned correction, or turn learning off. This data never
leaves the machine.

## Network access

LocalFlow makes exactly three kinds of connection, and two of them are to your
own computer:

| Destination | When | Purpose |
|---|---|---|
| `127.0.0.1:<ephemeral>` | Always | The desktop shell talking to its own backend. Bound to loopback with a random per-launch token; the shell refuses to connect to any non-loopback host. |
| `127.0.0.1:11434` | Only when AI cleanup is on | Ollama, running on your machine. Configurable, but LocalFlow will not use a remote host. |
| `huggingface.co` | Only when downloading a speech model you have not used before | Fetching model weights. Never sends audio or text - only the model name. |
| `ollama.com` registries | Only when *you* click Download on a language model | Fetching model weights. |

After the models are downloaded, LocalFlow works with the network disconnected.
Dictation, history, vocabulary and snippets all continue to work offline.

## What never happens

- No audio, transcript or text is sent anywhere.
- No telemetry, crash reporting or usage analytics - there is no code to
  disable, because none was written.
- No update check, no licence check, no account.
- Password fields are detected through UI Automation and their contents are
  never read; the language model is disabled while one is focused.
- The surrounding-text feature reads the text near your cursor to decide
  capitalisation and spacing. That text is used only to disambiguate, is capped
  at 600 characters, is never stored, and only reaches the local model when AI
  cleanup is on.

## Verifying it yourself

The Privacy page in the app reports the live state of each of these. For an
independent check:

```powershell
# Nothing should be listening on anything but loopback
netstat -ano | findstr LISTENING | findstr localflow

# Watch every connection the process makes
# (Resource Monitor → Network → filter by localflow.exe)
```

The code is short enough to read: `backend/localflow/server.py` is the only
socket LocalFlow opens, and `backend/localflow/llm/provider.py` is the only
outbound HTTP client.

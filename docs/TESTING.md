# Testing

```powershell
powershell -File scripts/build.ps1 -Test       # everything
.\.venv\Scripts\python.exe -m pytest -q        # Python only
cd desktop/src-tauri && cargo test             # Rust only
cd desktop && npm run typecheck                # TypeScript only
```

## What is covered

| Suite | Location | Covers |
|---|---|---|
| Unit - processing | `tests/unit/` | Self-correction, fillers, vocabulary, lists, punctuation, validation |
| Unit - VAD | `tests/unit/test_vad.py` | Silero finds speech in real speech |
| Unit - list shapes | `tests/unit/test_list_series*.py` | Every punctuation shape a spoken list arrives in |
| Unit - Rust | `desktop/src-tauri/src/*.rs` | Hotkey parsing, sentence-boundary maths |
| Integration | `tests/integration/` | The real backend process over its real WebSocket protocol |
| Benchmark | `benchmarks/run_benchmark.py` | Dictation quality, end to end, on spoken audio |
| Model comparison | `benchmarks/compare_models.py` | WER, latency and VRAM per model |

The integration test is the one that matters most: it spawns
`python -m localflow` exactly the way the desktop shell does, connects over the
loopback socket, and exercises the contract Rust depends on - settings
round-trips, the processing pipeline, vocabulary repair, snippet expansion,
command detection, history, and the privacy report. It also checks the things
that would be security bugs:

```python
test_bad_token_is_refused            # a wrong token closes the connection
test_unknown_methods_are_rejected    # "os.system" is not reachable
test_malformed_input_does_not_crash  # garbage in, backend still answering
```

## Regression tests worth knowing about

Two suites exist because of failures that produced no error anywhere, which
is the kind this project is most exposed to: the pipeline is a long chain of
transformations and a silent no-op in the middle looks like working software.

**`test_vad.py`** asserts that Silero returns a high speech probability on
recorded speech. The v5 ONNX graph has dynamic axes, so feeding it a bare
512 sample frame instead of the 64 samples of context plus 512 it expects
runs perfectly happily and returns near zero for everything. Every dictation
was transcribed correctly and then discarded as silence. Nothing logged a
problem; the only symptom was text that never arrived. The test asserts a
peak above 0.9, with the broken value having been 0.16 against a decision
threshold of 0.45.

**`test_list_series.py` and `test_list_series_shapes.py`** pin the list
detector to the utterance rather than to the punctuation. Whether a colon,
a full stop, a relative clause or a bare comma separates an announcement
from its items is decided by Whisper, or by the cleanup model, or by neither.
The tests assert that all of those shapes produce the same list, and that
prose with commas in it produces none.

Both suites also assert the negative case. A list detector that bullets
ordinary prose is worse than one that misses a list.

## Audio fixtures

Real spoken audio, not synthetic tones:

```powershell
powershell -File scripts/make_test_audio.ps1
```

This drives the Windows speech synthesiser to produce eleven WAV files covering
normal speech, filler-heavy speech, self-correction, numbers, dates, lists,
technical terms, an email, a question, a long dictation, and one with leading
and trailing silence for the VAD trimmer.

**The honest caveat:** synthesised speech is cleaner and more evenly paced than
a real voice. Treat every word error rate measured here as a floor, not a
prediction. It is why `compare_models.py --noisy` exists - it adds pink noise at
8 dB SNR and an attenuated variant, and that is the configuration that actually
separates a small model from a large one.

## The quality benchmark

```powershell
.\.venv\Scripts\python.exe benchmarks/run_benchmark.py
.\.venv\Scripts\python.exe benchmarks/run_benchmark.py --no-audio --no-llm   # fast
.\.venv\Scripts\python.exe benchmarks/run_benchmark.py --json results.json -v
```

40 text cases in `benchmarks/cases.json`, grouped by what they test: normal
speech, fillers, self-correction, numbers, lists, vocabulary, snippets,
commands, developer text, context, punctuation, style, multilingual, long
dictation, edge cases and safety.

Each case declares what must be true of the output rather than one exact string,
which is what lets the same case pass with or without the language model:

```json
{
  "id": "correction-mid-sentence",
  "input": "can you send Rahul the quotation tomorrow, actually no, Friday, ...",
  "must_contain": ["Friday", "Rahul", "customs"],
  "must_not_contain": ["tomorrow", "actually"]
}
```

### The metric that matters

**Zero-edit rate** - how often the output can be accepted without touching it.
Everything else is diagnostic.

Current results on the reference machine:

| | |
|---|---|
| Zero-edit rate | **40/40 (100 %)** |
| Language model invoked | 12.5 % of cases |
| Deterministic processing, median | 0.9 ms |
| End-to-end on spoken audio, median | ~700 ms |
| Realtime factor | 0.16× |

100 % is a statement about *this* 40-case suite, not about all speech. The suite
is there to catch regressions and to force every new behaviour to be specified
before it is built; a case that never fails is a case that has stopped earning
its place.

### Reading the word error rates

`benchmarks/metrics.py` normalises numbers, ordinals and separators before
scoring, because raw WER punishes correct behaviour. When the script says
"four thousand five hundred rupees" and Whisper writes "Rs 4,500", that is not
an error - it is the recogniser doing the right thing. Similarly "FastAPI" for
"fast API" is arguably better than the reference.

Residual WER on the fixtures is almost entirely these formatting differences.
Read `perfect_clips` alongside it.

## Testing against real applications

The automated suite cannot verify text insertion into other programs - that
needs a human. The manual checklist:

1. Launch LocalFlow; confirm it appears in the tray.
2. In each target application, click into a text field, hold `Ctrl+Space`, say
   *"Can you send Rahul the quotation on Friday"*, release.
3. Confirm the text lands at the cursor, correctly punctuated.
4. Press `Ctrl+Z`; confirm it is removed in one step.

| Application | Expect |
|---|---|
| Notepad | Plain insertion |
| Chrome / Edge address bar | Plain insertion |
| Gmail compose | Professional phrasing, paragraph breaks preserved |
| Google Docs | Insertion at caret; surrounding-text continuation works |
| Slack / Discord | Conversational phrasing, clipboard paste |
| WhatsApp Desktop | Conversational phrasing |
| Word | Document formatting, single undo step |
| VS Code / Cursor | Identifiers and paths preserved verbatim |
| Windows Terminal | **Verbatim** - no rewriting, no added punctuation |
| ChatGPT / Claude | Direct prompt text, no preamble |

Then the context cases:

- Type `Hi Rahul,` + Enter + `I wanted to let you know that ` and dictate
  *"the shipment has been delayed"* - it should continue in lower case.
- Select an existing sentence and dictate a replacement - it should overwrite,
  not append.
- Dictate into a password field - nothing should be read, and the Diagnostics
  page should show `is_password: true`.

## Developer tools

With Settings › Advanced › Developer mode on, the Diagnostics page shows
per-stage latency for the last dictation, the raw transcript, the deterministic
result and the final text, a pipeline sandbox for running text through without
speaking, and a probe that reports what LocalFlow can see about the focused
window.

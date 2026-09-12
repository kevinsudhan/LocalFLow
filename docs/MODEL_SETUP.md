# Model setup

LocalFlow runs two models: **Whisper** transcribes every dictation, and an
optional **language model** cleans up the ones that need it. They share your
GPU, so the interesting question is not "which is best" but "which pair fits".

Everything here is measured on the reference machine - RTX 3060 Laptop, 6 GB
VRAM, ~4.6 GB free with a desktop session running. Reproduce it with:

```powershell
.\.venv\Scripts\python.exe benchmarks/compare_models.py --asr --noisy --llm
```

---

## Speech recognition

### What the numbers say

33 clips: the spoken fixtures, plus each one degraded with pink noise at 8 dB
SNR and each one attenuated to simulate a distant speaker.

| Model | WER | Clips perfect | VRAM | Median | Download |
|---|---|---|---|---|---|
| `medium` | **0.037** | 27/33 | 1992 MB | 822 ms | 1.5 GB |
| `large-v3-turbo` | 0.043 | 27/33 | 2120 MB | **663 ms** | 1.6 GB |
| `small` | 0.060 | 23/33 | **755 MB** | 389 ms | 484 MB |

**On clean audio all three score 9/11** - identical. Clean speech simply does
not separate model sizes, which is why the noisy variants exist. The moment the
audio gets realistic, `small` makes about 60 % more errors than the other two.

### The default: `large-v3-turbo`

It matches `medium` on errors while being 20 % faster, and it is built on the
large-v3 encoder, which is materially stronger on non-English speech and on
code-switching than `medium` is. That last point is inherited from the model
lineage rather than measured here - the fixtures are English, because the
Windows speech synthesiser only has English voices.

### When to choose something else

| Situation | Choose |
|---|---|
| No NVIDIA GPU | `small` or `base` on CPU, INT8 - LocalFlow picks this automatically |
| Under 3 GB VRAM free | `small` |
| 8 GB+ VRAM free | `large-v3` - the most accurate model there is |
| You only ever dictate English and want minimum latency | `distil-large-v3` |
| Latency matters more than accuracy | `small` |

LocalFlow picks for you on first run. The Models page shows the reasoning
("Large v3 Turbo is the most accurate model that fits in 2835 MB of VRAM while
leaving 1800 MB for the local language model") and every model is one click.

### Precision

`float16` on GPU, `int8` on CPU, chosen automatically. When a model would
otherwise not fit, LocalFlow prefers **quantising a larger model** over dropping
to a smaller one - INT8 costs far less accuracy than four model sizes.

---

## The language model

This model is a *copy editor*, not an assistant. It is asked to produce the text
you meant to type, never to respond to it. It runs on roughly 12 % of
dictations - only when the deterministic pipeline flags something it cannot
resolve.

### What the numbers say

9 editing cases, run with Whisper already resident on the GPU, so the VRAM
pressure is realistic.

| Model | Passed | Median | On GPU | Notes |
|---|---|---|---|---|
| `qwen2.5:7b-instruct` | **7/9** | 1590 ms | 36 % | Most accurate. Only one that kept Tamil untranslated. |
| `qwen2.5:3b-instruct` | 6/9 | **640 ms** | 71 % | Best speed. Occasionally answers instead of editing. |
| `gemma3:4b-it-q4_K_M` | 6/9 | 596 ms | - | Middle ground. |
| `llama3.1:8b` | 4/9 | 1131 ms | - | Invents content under a style instruction. |
| `qwen3:4b` | **0/9** | timeout | - | See below. |

### The default: `qwen2.5:7b-instruct`

It is the only model tested that reliably keeps non-English text untranslated
and never slips into answering the dictation. With Whisper resident it only
partially fits on a 6 GB card, so Ollama offloads the rest to the CPU and it
takes about a second longer. That cost lands on 12 % of dictations; the accuracy
benefit lands on the hardest ones.

**If you want it faster**, switch to `qwen2.5:3b-instruct` in Settings › Models.
It is ~950 ms quicker and fits entirely in VRAM.

### Avoid reasoning models

`qwen3`, `deepseek-r1`, `qwq` and similar score **zero** here. They spend their
token budget reasoning about the request and then emit that reasoning as the
answer:

> We are given a transcript: "Can you send Rahul the report tomorrow?" The
> application is Notepad, so we are to…

LocalFlow defends against this - `clean_model_output` strips `<think>` blocks
and the validator rejects the rest, so you get the deterministic result rather
than nonsense - but the model is wasted. They are demoted in the automatic
recommendation.

---

## Downloading models

### From inside the app

Settings › Models. The **"Use any Ollama model"** box accepts anything Ollama
accepts, not just a curated list:

```
qwen2.5:7b-instruct               # the Ollama library
llama3.2:3b-instruct-q4_K_M       # a specific quantisation
hf.co/bartowski/some-model:Q4_K_M # any GGUF on Hugging Face
```

Progress, cancellation and deletion are all in the interface. Whisper models
download automatically the first time you select one.

### From the command line

```powershell
ollama pull qwen2.5:7b-instruct

# Whisper models, into LocalFlow's own cache
$env:HF_HUB_DISABLE_XET=1
.\.venv\Scripts\python.exe -c @"
from huggingface_hub import snapshot_download
snapshot_download('mobiuslabsgmbh/faster-whisper-large-v3-turbo',
                  cache_dir=r'$env:APPDATA\LocalFlow\models\whisper')
"@
```

> `HF_HUB_DISABLE_XET=1` is not optional on some networks. Hugging Face's Xet
> transfer backend has been observed to stall at zero bytes on Windows, which
> looks exactly like a hung download. LocalFlow sets this automatically;
> `LOCALFLOW_ENABLE_XET=1` opts back in.

---

## Fitting both on one GPU

| Free VRAM | Whisper | Language model | Result |
|---|---|---|---|
| 12 GB+ | `large-v3` | `qwen2.5:7b-instruct` | Both fully resident |
| 6 GB | `large-v3` | 3B class | Both resident |
| 4.6 GB (reference) | `large-v3-turbo` | `qwen2.5:7b-instruct` | LLM partly on CPU, +1 s when it runs |
| 4.6 GB (fast) | `large-v3-turbo` | `qwen2.5:3b-instruct` | Both resident |
| 3 GB | `small` | 3B class | Tight; consider disabling AI cleanup |
| No GPU | `small` INT8 on CPU | 1.5B class or off | Works, a few seconds per dictation |

LocalFlow reserves 1800 MB for the language model when choosing Whisper. That is
deliberately less than a resident 3B model needs: Whisper runs on *every*
dictation and must stay resident, while Ollama evicts the language model when
`keep_alive` expires. When they do overlap, Ollama offloads layers to the CPU -
costing latency on one dictation rather than accuracy on all of them.

To free VRAM at any point: Settings › Models › **Free memory** on either model,
or set **Keep loaded for** to "Unload immediately".

---

## Voice activity detection

Silero VAD (2.2 MB ONNX) trims silence around your speech. `scripts/setup.ps1`
downloads it; `scripts/download_vad.py` re-runs that step on its own.

Without it LocalFlow falls back to an energy-based detector. That works, but it
is noticeably worse in a noisy room, and the Diagnostics page reports which one
is active rather than silently substituting.

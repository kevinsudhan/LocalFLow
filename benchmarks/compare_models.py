"""Compare speech and language models on this machine's real hardware.

Picking a model from published benchmark tables is guesswork: what matters is
accuracy *per millisecond* on a 6 GB laptop GPU that also has to hold the
editing model. This measures both.

    python benchmarks/compare_models.py --asr
    python benchmarks/compare_models.py --llm
    python benchmarks/compare_models.py --asr --llm --json out.json
"""
from __future__ import annotations

import argparse
import gc
import json
import re
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from localflow.asr.gpu import nvidia_smi, prepare_cuda  # noqa: E402

from metrics import word_error_rate  # noqa: E402

prepare_cuda()

FIXTURES = ROOT / "tests" / "fixtures" / "audio"
MODELS_DIR = ROOT / ".devdata" / "models" / "whisper"

ASR_CANDIDATES = [
    ("small", "Systran/faster-whisper-small", "float16"),
    ("medium", "Systran/faster-whisper-medium", "float16"),
    ("large-v3-turbo", "mobiuslabsgmbh/faster-whisper-large-v3-turbo", "float16"),
]

LLM_CANDIDATES = [
    "qwen2.5:3b-instruct",
    "qwen3:4b",
    "gemma3:4b-it-q4_K_M",
    "qwen2.5:7b-instruct",
    "llama3.1:8b",
]

def vram_used_mb() -> int:
    info = nvidia_smi.__wrapped__() if hasattr(nvidia_smi, "__wrapped__") else nvidia_smi()
    return int((info or {}).get("vram_used_mb", 0))



def add_noise(audio, snr_db: float):
    """Mix in speech-shaped noise at a given signal-to-noise ratio.

    Pink-ish noise (1/f) approximates room and fan noise far better than white
    noise, and it overlaps the speech band where it actually causes errors.
    """
    import numpy as np

    rng = np.random.default_rng(1234)
    white = rng.standard_normal(audio.size).astype(np.float32)
    # One-pole low-pass gives a 1/f-ish tilt without needing a filter design.
    pink = np.empty_like(white)
    state = 0.0
    for i in range(white.size):
        state = 0.97 * state + 0.03 * white[i]
        pink[i] = state
    pink /= max(float(np.sqrt(np.mean(pink**2))), 1e-9)

    speech_rms = float(np.sqrt(np.mean(audio**2))) or 1e-9
    noise_rms = speech_rms / (10 ** (snr_db / 20))
    mixed = audio + pink * noise_rms
    peak = float(np.max(np.abs(mixed))) or 1.0
    return (mixed / max(peak, 1.0)).astype("float32")


def attenuate(audio, gain: float):
    """A quiet, distant speaker."""
    import numpy as np

    return (np.asarray(audio) * gain).astype("float32")


def compare_asr(noisy: bool = False) -> list[dict]:
    import soundfile as sf
    from faster_whisper import WhisperModel

    from localflow.audio.resample import resample_to_16k, to_mono

    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8-sig"))
    clips = []
    for name, reference in manifest.items():
        path = FIXTURES / f"{name}.wav"
        if path.is_file():
            raw, rate = sf.read(str(path), dtype="float32")
            audio = resample_to_16k(to_mono(raw), rate)
            clips.append((name, reference, audio))
            if noisy:
                # Synthesised speech is unnaturally clean, so the clean set
                # cannot separate a small model from a large one. Degrading it
                # the way a real room does is what exposes the difference.
                clips.append((f"{name}+noise", reference, add_noise(audio, snr_db=8.0)))
                clips.append((f"{name}+quiet", reference, attenuate(audio, gain=0.14)))

    results = []
    for key, repo, compute in ASR_CANDIDATES:
        print(f"\n=== {key} ({compute}) ===", flush=True)
        baseline = vram_used_mb()
        started = time.perf_counter()
        try:
            model = WhisperModel(
                repo, device="cuda", compute_type=compute, download_root=str(MODELS_DIR)
            )
        except Exception as exc:
            print(f"  unavailable: {type(exc).__name__}: {str(exc)[:160]}")
            continue
        load_s = time.perf_counter() - started

        # Warm up so the first clip is not charged for lazy CUDA init.
        list(model.transcribe(clips[0][2], beam_size=5)[0])
        resident = max(0, vram_used_mb() - baseline)

        rows = []
        for name, reference, audio in clips:
            started = time.perf_counter()
            segments, _info = model.transcribe(audio, beam_size=5, vad_filter=False)
            text = "".join(s.text for s in segments).strip()
            elapsed = (time.perf_counter() - started) * 1000
            wer = word_error_rate(reference, text)
            rows.append(
                {
                    "clip": name,
                    "wer": round(wer, 4),
                    "ms": round(elapsed, 1),
                    "seconds": round(audio.size / 16000, 2),
                    "text": text,
                }
            )
            flag = "  " if wer == 0 else "!!"
            print(f"  {flag} {name:<16} WER {wer:.3f}  {elapsed:7.0f} ms")
            if wer > 0:
                print(f"       ref: {reference}")
                print(f"       got: {text}")

        total_audio = sum(r["seconds"] for r in rows)
        total_ms = sum(r["ms"] for r in rows)
        summary = {
            "model": key,
            "repo": repo,
            "compute_type": compute,
            "load_seconds": round(load_s, 1),
            "vram_mb": resident,
            "mean_wer": round(sum(r["wer"] for r in rows) / len(rows), 4),
            "perfect_clips": sum(1 for r in rows if r["wer"] == 0),
            "clips": len(rows),
            "total_ms": round(total_ms, 1),
            "realtime_factor": round(total_ms / 1000 / max(total_audio, 0.01), 3),
            "median_ms": round(sorted(r["ms"] for r in rows)[len(rows) // 2], 1),
            "rows": rows,
        }
        print(
            f"  -> mean WER {summary['mean_wer']:.4f} | {summary['perfect_clips']}/{len(rows)}"
            f" perfect | {summary['vram_mb']} MB | x{summary['realtime_factor']:.3f} realtime"
        )
        results.append(summary)
        del model
        gc.collect()
        time.sleep(1.0)
    return results


# Cases where the LLM actually runs; these are what an editor model is for.
LLM_CASES = [
    {
        "id": "no-answering",
        "input": "Can you send Rahul the report tomorrow?",
        "must_not": ["sure", "i'll", "i will send", "certainly", "of course"],
        "must": ["Rahul", "report"],
    },
    {
        "id": "unresolved-correction",
        "input": "The plan is fine, actually let me redo the whole thing.",
        "must": ["redo"],
    },
    {
        "id": "run-on",
        "input": (
            "good morning everyone I wanted to give a quick update on the shipment status the "
            "container cleared customs yesterday evening and is now on its way to the warehouse "
            "we expect delivery by Thursday afternoon"
        ),
        "must": ["customs", "warehouse", "Thursday"],
        "min_sentences": 2,
    },
    {
        "id": "professional-no-invention",
        "input": "hey can you push the delivery to next week we are short on stock",
        "style": "professional",
        "must": ["delivery", "stock"],
        "no_invention": True,
    },
    {
        "id": "casual-no-invention",
        "input": "running five minutes late for the call",
        "style": "casual",
        "must": ["five minutes late"],
        "no_invention": True,
    },
    {
        "id": "preserve-technical",
        "input": "update the getShipmentStatus handler in shipment_service.py and redeploy",
        "style": "developer",
        "must": ["getShipmentStatus", "shipment_service.py"],
    },
    {
        "id": "preserve-numbers",
        "input": "the invoice is 4500 rupees due on the 15th of March please confirm",
        "must": ["4500", "15th", "March"],
    },
    {
        "id": "multilingual-keep",
        "input": "Tomorrow shipment வந்து சேரும் so please inform the customer",
        "must": ["வந்து சேரும்", "customer"],
    },
    {
        "id": "filler-heavy",
        "input": "um so basically I think we should uh ship the order today you know",
        "must": ["ship the order today"],
        "must_not": ["basically", "you know"],
    },
]


def compare_llm(models: list[str]) -> list[dict]:
    from localflow.llm.prompt import PromptBuilder, PromptContext
    from localflow.llm.provider import LlmError, OllamaProvider, clean_model_output
    from localflow.processing import style as style_module
    from localflow.processing.validate import added_content_words, validate_llm_output

    provider = OllamaProvider(timeout=60.0)
    available = {m.name for m in provider.list_models()}
    prompts = PromptBuilder()
    results = []

    for model in models:
        if model not in available:
            print(f"\n=== {model}: not installed, skipping ===")
            continue
        print(f"\n=== {model} ===", flush=True)
        provider.warm(model)
        rows = []
        for case in LLM_CASES:
            style_key = case.get("style", "neutral")
            ctx = PromptContext(
                transcript=case["input"],
                application="Notepad",
                style_instruction=style_module.instruction_for(style_key),
                vocabulary=["ICEGATE", "GSTIN", "Ollama"],
            )
            system, user = prompts.build(ctx)
            started = time.perf_counter()
            try:
                response = provider.generate(
                    system, user, model=model, timeout=60.0, temperature=0.0, num_ctx=4096
                )
                text = clean_model_output(response.text)
                error = ""
            except LlmError as exc:
                text, error = "", str(exc)
            elapsed = (time.perf_counter() - started) * 1000

            reasons = []
            if error:
                reasons.append(error[:60])
            # Compare with thousands separators collapsed: a model writing
            # "4,500" for "4500" has reformatted, not lost, the number.
            haystack = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", text).lower()
            for needle in case.get("must", []):
                if needle.lower() not in haystack:
                    reasons.append(f"missing {needle!r}")
            for needle in case.get("must_not", []):
                if needle.lower() in text.lower():
                    reasons.append(f"has {needle!r}")
            if case.get("min_sentences"):
                if len(re.findall(r"[.!?]", text)) < case["min_sentences"]:
                    reasons.append("not split into sentences")
            if case.get("no_invention"):
                added = added_content_words(case["input"], text)
                if added:
                    reasons.append("invented " + ",".join(added[:3]))
            verdict = validate_llm_output(case["input"], text) if text else None
            if verdict is not None and not verdict.ok:
                reasons.append("rejected:" + verdict.reason)

            passed = not reasons
            rows.append(
                {
                    "case": case["id"],
                    "passed": passed,
                    "reasons": reasons,
                    "ms": round(elapsed, 1),
                    "output": text,
                }
            )
            print(f"  {'PASS' if passed else 'FAIL'} {case['id']:<26} {elapsed:6.0f} ms")
            if not passed:
                print(f"       {'; '.join(reasons)[:110]}")
                print(f"       out: {text[:110]}")

        running = provider.running()
        vram = next(
            (int(m.get("size_vram", 0)) // (1024 * 1024) for m in running if m.get("name") == model),
            0,
        )
        summary = {
            "model": model,
            "passed": sum(r["passed"] for r in rows),
            "total": len(rows),
            "pass_rate": round(sum(r["passed"] for r in rows) / len(rows), 3),
            "median_ms": round(sorted(r["ms"] for r in rows)[len(rows) // 2], 1),
            "max_ms": round(max(r["ms"] for r in rows), 1),
            "vram_mb": vram,
            "rows": rows,
        }
        print(
            f"  -> {summary['passed']}/{summary['total']} passed | median "
            f"{summary['median_ms']:.0f} ms | {vram} MB VRAM"
        )
        results.append(summary)
        provider.unload(model)
        time.sleep(1.0)
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asr", action="store_true")
    parser.add_argument(
        "--noisy",
        action="store_true",
        help="also test noisy and quiet variants, which is what separates model sizes",
    )
    parser.add_argument("--llm", action="store_true")
    parser.add_argument("--models", default="")
    parser.add_argument("--json", default="")
    args = parser.parse_args()
    if not args.asr and not args.llm:
        args.asr = args.llm = True

    payload: dict = {}
    if args.asr:
        payload["asr"] = compare_asr(noisy=args.noisy)
    if args.llm:
        models = [m.strip() for m in args.models.split(",") if m.strip()] or LLM_CANDIDATES
        payload["llm"] = compare_llm(models)

    print("\n" + "=" * 78)
    if payload.get("asr"):
        print("Speech models (lower WER is better; VRAM shared with the editing model)")
        print(f"  {'model':<18}{'WER':>8}{'perfect':>9}{'VRAM':>8}{'median':>9}{'realtime':>10}")
        for row in sorted(payload["asr"], key=lambda r: r["mean_wer"]):
            print(
                f"  {row['model']:<18}{row['mean_wer']:>8.4f}"
                f"{row['perfect_clips']}/{row['clips']:>7}{row['vram_mb']:>7} MB"
                f"{row['median_ms']:>8.0f} ms{row['realtime_factor']:>9.3f}x"
            )
    if payload.get("llm"):
        print("\nEditing models (higher pass rate is better)")
        print(f"  {'model':<26}{'passed':>9}{'median':>10}{'VRAM':>9}")
        for row in sorted(payload["llm"], key=lambda r: (-r["pass_rate"], r["median_ms"])):
            print(
                f"  {row['model']:<26}{row['passed']}/{row['total']:>7}"
                f"{row['median_ms']:>8.0f} ms{row['vram_mb']:>6} MB"
            )

    if args.json:
        Path(args.json).write_text(json.dumps(payload, indent=2, ensure_ascii=False), "utf-8")
        print(f"\nWrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

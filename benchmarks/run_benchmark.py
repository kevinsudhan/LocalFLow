"""LocalFlow dictation quality benchmark.

Runs the real pipeline over the spoken fixtures and the text-only cases and
reports the metrics that matter for a dictation product:

* **zero-edit rate** - how often the output can be accepted as-is. This is the
  headline number; everything else is diagnostic.
* **word error rate** on the ASR stage alone.
* **correction accuracy** - superseded content actually removed.
* **latency** broken down by stage.

    python benchmarks/run_benchmark.py            # everything
    python benchmarks/run_benchmark.py --no-audio # text cases only (fast)
    python benchmarks/run_benchmark.py --json out.json
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from pathlib import Path

# Fixtures include Tamil and Hinglish; the Windows console default would raise.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from localflow.asr.gpu import prepare_cuda  # noqa: E402

prepare_cuda()

from localflow.service import LocalFlowService  # noqa: E402

from metrics import word_error_rate  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "audio"
CASES_FILE = ROOT / "benchmarks" / "cases.json"


def matches_expectation(text: str, case: dict) -> tuple[bool, str]:
    """A case passes when every requirement holds."""
    lowered = text.lower()
    for needle in case.get("must_contain", []):
        if needle.lower() not in lowered:
            return False, f"missing {needle!r}"
    for needle in case.get("must_not_contain", []):
        if needle.lower() in lowered:
            return False, f"should not contain {needle!r}"
    for pattern in case.get("must_match", []):
        if not re.search(pattern, text, re.IGNORECASE):
            return False, f"does not match /{pattern}/"
    if case.get("expected") and text.strip() != case["expected"].strip():
        return False, "differs from the expected text"
    return True, ""


def run_text_cases(service: LocalFlowService, cases: list[dict], use_llm: bool) -> list[dict]:
    results = []
    for case in cases:
        started = time.perf_counter()
        outcome = service.process_text(
            case["input"],
            context=case.get("context", {"exe": "notepad.exe"}),
            style=case.get("style", ""),
            force_llm=None if use_llm else False,
        )
        elapsed = (time.perf_counter() - started) * 1000
        ok, reason = matches_expectation(outcome["text"], case)
        results.append(
            {
                "id": case["id"],
                "group": case.get("group", "general"),
                "input": case["input"],
                "output": outcome["text"],
                "passed": ok,
                "reason": reason,
                "used_llm": outcome["used_llm"],
                "gate": outcome["llm_reason"],
                "latency_ms": round(elapsed, 1),
            }
        )
    return results


def run_audio_cases(service: LocalFlowService, use_llm: bool) -> list[dict]:
    import soundfile as sf

    from localflow.audio.resample import resample_to_16k, to_mono
    from localflow.context.engine import ContextSnapshot
    from localflow.processing.pipeline import PipelineInput

    manifest_path = FIXTURES / "manifest.json"
    if not manifest_path.is_file():
        print("No audio fixtures. Run scripts/make_test_audio.ps1 first.")
        return []
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))

    service.asr.ensure_loaded()
    service.vad.load()
    results = []

    for name, reference in manifest.items():
        wav = FIXTURES / f"{name}.wav"
        if not wav.is_file():
            continue
        raw, rate = sf.read(str(wav), dtype="float32")
        audio = resample_to_16k(to_mono(raw), rate)

        started = time.perf_counter()
        vad_started = time.perf_counter()
        vad_result = service.vad.process(audio)
        vad_ms = (time.perf_counter() - vad_started) * 1000

        transcription = service.asr.transcribe(vad_result.audio)
        asr_ms = transcription.latency_ms

        snapshot = ContextSnapshot.from_dict({"exe": "notepad.exe"})
        resolved = service.context_engine.resolve(snapshot, llm_globally_enabled=use_llm)
        process_started = time.perf_counter()
        outcome = service.pipeline.run(
            PipelineInput(
                raw_text=transcription.text,
                context=resolved,
                segments=transcription.segments,
                language=transcription.language,
                avg_logprob=transcription.avg_logprob,
            )
        )
        process_ms = (time.perf_counter() - process_started) * 1000
        total_ms = (time.perf_counter() - started) * 1000

        results.append(
            {
                "id": f"audio:{name}",
                "group": "audio",
                "reference": reference,
                "raw": transcription.text,
                "output": outcome.text,
                "wer": round(word_error_rate(reference, transcription.text), 4),
                "audio_seconds": round(vad_result.total_seconds, 2),
                "speech_seconds": round(vad_result.speech_seconds, 2),
                "trimmed_lead": round(vad_result.trimmed_lead, 2),
                "used_llm": outcome.used_llm,
                "gate": outcome.llm_reason or outcome.llm_skipped_reason,
                "latency": {
                    "vad_ms": round(vad_ms, 1),
                    "asr_ms": round(asr_ms, 1),
                    "process_ms": round(process_ms, 1),
                    "llm_ms": round(outcome.timings.get("llm", 0.0), 1),
                    "total_ms": round(total_ms, 1),
                },
                "realtime_factor": round(
                    total_ms / 1000 / max(vad_result.total_seconds, 0.01), 3
                ),
            }
        )
    return results


def summarise(text_results: list[dict], audio_results: list[dict]) -> dict:
    passed = [r for r in text_results if r["passed"]]
    by_group: dict[str, dict] = {}
    for row in text_results:
        bucket = by_group.setdefault(row["group"], {"passed": 0, "total": 0})
        bucket["total"] += 1
        bucket["passed"] += int(row["passed"])

    summary = {
        "text_cases": len(text_results),
        "text_passed": len(passed),
        "zero_edit_rate": round(len(passed) / len(text_results), 4) if text_results else 0.0,
        "by_group": {
            name: {**stats, "rate": round(stats["passed"] / stats["total"], 3)}
            for name, stats in sorted(by_group.items())
        },
        "llm_invocation_rate": (
            round(sum(r["used_llm"] for r in text_results) / len(text_results), 3)
            if text_results
            else 0.0
        ),
    }
    if text_results:
        summary["text_latency_ms"] = {
            "median": round(statistics.median(r["latency_ms"] for r in text_results), 1),
            "p95": round(
                sorted(r["latency_ms"] for r in text_results)[
                    max(0, int(len(text_results) * 0.95) - 1)
                ],
                1,
            ),
        }
    if audio_results:
        summary["audio_cases"] = len(audio_results)
        summary["mean_wer"] = round(
            statistics.mean(r["wer"] for r in audio_results), 4
        )
        summary["median_total_ms"] = round(
            statistics.median(r["latency"]["total_ms"] for r in audio_results), 1
        )
        summary["median_realtime_factor"] = round(
            statistics.median(r["realtime_factor"] for r in audio_results), 3
        )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-audio", action="store_true")
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--json", default="")
    parser.add_argument("--data-dir", default=str(ROOT / ".devdata"))
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    cases = json.loads(CASES_FILE.read_text(encoding="utf-8"))
    service = LocalFlowService(data_dir=Path(args.data_dir))
    use_llm = not args.no_llm and service.settings.llm.enabled

    print(f"LocalFlow benchmark - LLM {'on (' + service.settings.llm.model + ')' if use_llm else 'off'}")
    print("=" * 78)

    text_results = run_text_cases(service, cases, use_llm)
    audio_results = [] if args.no_audio else run_audio_cases(service, use_llm)

    for row in text_results:
        mark = "PASS" if row["passed"] else "FAIL"
        if not row["passed"] or args.verbose:
            print(f"[{mark}] {row['id']} ({row['group']})")
            print(f"       in : {row['input'][:110]}")
            print(f"       out: {row['output'][:110]}")
            if row["reason"]:
                print(f"       why: {row['reason']}")

    if audio_results:
        print("\nSpoken fixtures")
        print("-" * 78)
        for row in audio_results:
            print(
                f"  {row['id']:<24} WER {row['wer']:.3f}  "
                f"{row['latency']['total_ms']:>7.0f} ms  x{row['realtime_factor']:.2f} RT"
            )
            if args.verbose:
                print(f"       ref: {row['reference'][:100]}")
                print(f"       out: {row['output'][:100]}")

    summary = summarise(text_results, audio_results)
    print("\nSummary")
    print("-" * 78)
    print(json.dumps(summary, indent=2))

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {"summary": summary, "text": text_results, "audio": audio_results}, indent=2
            ),
            encoding="utf-8",
        )
        print(f"\nWrote {args.json}")

    service.shutdown()
    return 0 if summary["zero_edit_rate"] >= 0.8 else 1


if __name__ == "__main__":
    sys.exit(main())

"""Download the Silero VAD model into LocalFlow's data directory.

The model is ~2 MB and is the only asset LocalFlow needs before it can trim
silence properly. Without it the app still runs, using an energy-based fallback
that is noticeably worse in a noisy room - so the setup script fetches it up
front rather than surprising the user later.
"""
from __future__ import annotations

import hashlib
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from localflow import paths  # noqa: E402

SOURCES = (
    "https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/silero_vad.onnx",
    "https://raw.githubusercontent.com/snakers4/silero-vad/master/src/silero_vad/data/silero_vad.onnx",
)
MIN_BYTES = 500_000


def download(target: Path) -> bool:
    for url in SOURCES:
        try:
            print(f"  fetching {url}")
            request = urllib.request.Request(url, headers={"User-Agent": "LocalFlow/1.0"})
            with urllib.request.urlopen(request, timeout=60) as response:
                data = response.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            print(f"    failed: {exc}")
            continue

        if len(data) < MIN_BYTES:
            print(f"    unexpected size ({len(data)} bytes); trying the next source")
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".onnx.part")
        temporary.write_bytes(data)
        temporary.replace(target)
        digest = hashlib.sha256(data).hexdigest()[:16]
        print(f"  wrote {target} ({len(data) / 1024:.0f} KB, sha256 {digest}…)")
        return True
    return False


def main() -> int:
    paths.ensure_dirs()
    target = paths.MODELS_DIR / "silero_vad.onnx"
    if target.is_file() and target.stat().st_size > MIN_BYTES:
        print(f"  already present: {target}")
        return 0

    if not download(target):
        print(
            "\nCould not download the VAD model. LocalFlow will fall back to energy-based\n"
            "silence detection, which works but is less accurate in noisy rooms.\n"
            f"To fix it later, place silero_vad.onnx in {paths.MODELS_DIR}",
            file=sys.stderr,
        )
        return 1

    # Prove it actually loads before declaring success.
    try:
        from localflow.vad.silero import SileroVad

        engine = SileroVad(target)
        import numpy as np

        probabilities = engine.probabilities(np.zeros(16000, dtype=np.float32))
        print(f"  verified: {probabilities.size} frames analysed on silence")
    except Exception as exc:  # pragma: no cover - environment dependent
        print(f"  warning: the model downloaded but did not load ({exc})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

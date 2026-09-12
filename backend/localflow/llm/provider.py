"""Local LLM providers.

``LocalLLMProvider`` is the seam the spec asks for: nothing above this module
knows that Ollama exists, and no model name is hard-coded anywhere.  The
concrete Ollama provider talks to the local daemon over HTTP on 127.0.0.1 only.
"""
from __future__ import annotations

import json
import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterator

import httpx

# Fallback when no keep-alive is supplied. Kept short on purpose: see the note
# on LlmSettings.keep_alive - a language model squatting in VRAM starves the
# speech model that runs on every single dictation.
DEFAULT_KEEP_ALIVE = "30s"

log = logging.getLogger(__name__)


class LlmError(RuntimeError):
    def __init__(self, code: str, message: str, detail: str = ""):
        super().__init__(message)
        self.code = code
        self.detail = detail


@dataclass
class LlmModel:
    name: str
    size_bytes: int = 0
    parameter_size: str = ""
    quantization: str = ""
    family: str = ""

    @property
    def size_gb(self) -> float:
        return round(self.size_bytes / (1024 ** 3), 2)


@dataclass
class LlmResult:
    text: str
    model: str
    latency_ms: float
    prompt_tokens: int = 0
    output_tokens: int = 0
    truncated: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class PullProgress:
    status: str
    completed: int = 0
    total: int = 0
    digest: str = ""
    done: bool = False
    error: str = ""

    @property
    def percent(self) -> float:
        return round(100.0 * self.completed / self.total, 1) if self.total else 0.0


class LocalLLMProvider(ABC):
    """Interface every local LLM backend implements."""

    name = "provider"

    @abstractmethod
    def list_models(self) -> list[LlmModel]: ...

    @abstractmethod
    def available(self) -> tuple[bool, str]: ...

    @abstractmethod
    def generate(self, system: str, user: str, **kwargs: Any) -> LlmResult: ...

    @abstractmethod
    def stream(self, system: str, user: str, **kwargs: Any) -> Iterator[str]: ...

    def warm(self, model: str) -> bool:
        return False

    def unload(self, model: str) -> bool:
        return False

    def pull(self, model: str, on_progress=None, cancel=None) -> PullProgress:
        raise LlmError("not_supported", "This provider cannot download models.")

    def delete(self, model: str) -> bool:
        return False


# Any name Ollama itself accepts: [host/][namespace/]model[:tag], including
# `hf.co/user/repo:Q4_K_M`.  Validated so a typo fails fast with a clear
# message rather than a confusing 404 from the daemon.
MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]*(?:[/:][A-Za-z0-9._\-]+)*$")
MAX_MODEL_NAME = 200


def validate_model_name(model: str) -> str:
    name = (model or "").strip()
    if not name:
        raise LlmError("llm_no_model", "Enter a model name.")
    if len(name) > MAX_MODEL_NAME or not MODEL_NAME_RE.match(name):
        raise LlmError(
            "llm_bad_model_name",
            f"'{name[:60]}' is not a valid Ollama model name. Use a name like "
            "'qwen2.5:3b-instruct' or 'hf.co/user/repo:Q4_K_M'.",
        )
    return name


# Models that are a poor fit for a strict editing task, or too large for a
# 6 GB laptop GPU to hold alongside Whisper.  Used only for ranking.
_PREFERRED_FAMILIES = ("qwen", "llama", "mistral", "gemma", "phi")
_BAD_FOR_EDITING = (
    "embed", "vision", "code", "coder", "math", "moondream", "llava",
    # Reasoning models spend their budget thinking instead of editing.
    "r1", "reasoning", "think", "qwq", "marco-o1",
)


class OllamaProvider(LocalLLMProvider):
    name = "ollama"

    def __init__(self, base_url: str = "http://127.0.0.1:11434", timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client: httpx.Client | None = None
        self._models_cache: tuple[float, list[LlmModel]] | None = None

    # -- plumbing ----------------------------------------------------------
    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout, connect=2.5),
                trust_env=False,      # never route local traffic through a proxy
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def configure(self, base_url: str, timeout: float) -> None:
        if base_url.rstrip("/") != self.base_url or timeout != self.timeout:
            self.close()
            self._models_cache = None
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # -- discovery ---------------------------------------------------------
    def available(self) -> tuple[bool, str]:
        try:
            response = self._http().get("/api/tags", timeout=3.0)
            response.raise_for_status()
            return True, ""
        except httpx.ConnectError:
            return False, "Ollama isn't running. Start Ollama and try again."
        except httpx.TimeoutException:
            return False, "Ollama did not respond. Check that the Ollama service is healthy."
        except Exception as exc:
            return False, f"Could not reach Ollama: {exc}"

    def list_models(self, max_age: float = 5.0) -> list[LlmModel]:
        now = time.time()
        if self._models_cache and now - self._models_cache[0] < max_age:
            return self._models_cache[1]
        try:
            response = self._http().get("/api/tags", timeout=4.0)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise LlmError("llm_unreachable", self.available()[1] or str(exc), str(exc)) from exc

        models: list[LlmModel] = []
        for item in payload.get("models", []):
            details = item.get("details") or {}
            models.append(
                LlmModel(
                    name=item.get("name", ""),
                    size_bytes=int(item.get("size", 0) or 0),
                    parameter_size=details.get("parameter_size", ""),
                    quantization=details.get("quantization_level", ""),
                    family=details.get("family", ""),
                )
            )
        models = [m for m in models if m.name]
        self._models_cache = (now, models)
        return models

    def recommend(self, vram_free_mb: int = 0) -> str:
        """Pick a sensible installed model for strict editing."""
        try:
            models = self.list_models()
        except LlmError:
            return ""
        if not models:
            return ""

        def score(model: LlmModel) -> tuple[int, float]:
            name = model.name.lower()
            points = 0
            if any(bad in name for bad in _BAD_FOR_EDITING):
                points -= 60
            # Instruction tuning matters more than size here: the task is
            # "follow these editing rules exactly", which base models fail.
            if "instruct" in name or "-it" in name or name.endswith(":it"):
                points += 30
            for index, family in enumerate(_PREFERRED_FAMILIES):
                if family in name:
                    points += 20 - index * 2
                    break
            gb = model.size_gb
            if vram_free_mb:
                # Whisper shares this GPU. A model that overflows still works -
                # Ollama offloads layers to the CPU - so this is a soft penalty.
                budget_gb = max(1.0, (vram_free_mb - 1400) / 1024.0)
                points += 20 if gb <= budget_gb else -12
            if 1.5 <= gb <= 5.0:
                points += 18
            elif gb < 1.5:
                points += 4
            elif gb > 9:
                points -= 25
            return points, -gb

        return max(models, key=score).name

    # -- generation --------------------------------------------------------
    def _options(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        options = {
            "temperature": kwargs.get("temperature", 0.0),
            "top_p": kwargs.get("top_p", 0.9),
            "num_ctx": kwargs.get("num_ctx", 4096),
            "repeat_penalty": 1.05,
            "stop": ["\n\nTranscript:", "</output>"],
        }
        if kwargs.get("num_predict"):
            options["num_predict"] = int(kwargs["num_predict"])
        return options

    def generate(self, system: str, user: str, **kwargs: Any) -> LlmResult:
        model = kwargs.get("model") or ""
        if not model:
            raise LlmError("llm_no_model", "No local model is selected.")
        payload = {
            "model": model,
            "system": system,
            "prompt": user,
            "stream": False,
            "keep_alive": kwargs.get("keep_alive", DEFAULT_KEEP_ALIVE),
            # Reasoning models default to emitting a long <think> block, which
            # for a copy-editing task is pure latency. Ignored by models that
            # have no thinking mode.
            "think": False,
            "options": self._options(kwargs),
        }
        timeout = float(kwargs.get("timeout", self.timeout))
        started = time.perf_counter()
        try:
            response = self._http().post("/api/generate", json=payload, timeout=timeout)
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException as exc:
            raise LlmError(
                "llm_timeout",
                f"The local model took longer than {timeout:.0f}s. LocalFlow used the "
                "cleaned transcript instead.",
                str(exc),
            ) from exc
        except httpx.ConnectError as exc:
            raise LlmError("llm_unreachable", "Ollama isn't running. Start Ollama and try again.",
                           str(exc)) from exc
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:400]
            if exc.response.status_code == 404:
                raise LlmError(
                    "llm_model_missing",
                    f"The model '{model}' is not downloaded yet. Download it from "
                    "Settings > AI, or run: ollama pull " + model,
                    detail,
                ) from exc
            if "memory" in detail.lower():
                raise LlmError(
                    "llm_out_of_memory",
                    "The local model did not fit in memory. Choose a smaller model in "
                    "Settings > AI.",
                    detail,
                ) from exc
            raise LlmError("llm_failed", "The local model returned an error.", detail) from exc
        except Exception as exc:
            raise LlmError("llm_failed", "The local model could not be reached.", str(exc)) from exc

        return LlmResult(
            text=(data.get("response") or "").strip(),
            model=model,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            prompt_tokens=int(data.get("prompt_eval_count", 0) or 0),
            output_tokens=int(data.get("eval_count", 0) or 0),
            truncated=data.get("done_reason") == "length",
            raw={k: v for k, v in data.items() if k != "response"},
        )

    def stream(self, system: str, user: str, **kwargs: Any) -> Iterator[str]:
        model = kwargs.get("model") or ""
        if not model:
            raise LlmError("llm_no_model", "No local model is selected.")
        payload = {
            "model": model,
            "system": system,
            "prompt": user,
            "stream": True,
            "keep_alive": kwargs.get("keep_alive", DEFAULT_KEEP_ALIVE),
            "options": self._options(kwargs),
        }
        try:
            with self._http().stream(
                "POST", "/api/generate", json=payload,
                timeout=float(kwargs.get("timeout", self.timeout)),
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                    except ValueError:
                        continue
                    piece = chunk.get("response")
                    if piece:
                        yield piece
                    if chunk.get("done"):
                        break
        except httpx.HTTPError as exc:
            raise LlmError("llm_failed", "Streaming from the local model failed.", str(exc)) from exc

    def warm(self, model: str) -> bool:
        """Load the model into memory so the first dictation is not the slow one."""
        if not model:
            return False
        try:
            self._http().post(
                "/api/generate",
                json={"model": model, "prompt": "", "keep_alive": DEFAULT_KEEP_ALIVE, "stream": False},
                timeout=90.0,
            ).raise_for_status()
            return True
        except Exception:
            log.debug("Warm-up for %s failed", model, exc_info=True)
            return False

    def unload(self, model: str) -> bool:
        if not model:
            return False
        try:
            self._http().post(
                "/api/generate",
                json={"model": model, "prompt": "", "keep_alive": 0, "stream": False},
                timeout=15.0,
            )
            return True
        except Exception:
            return False

    # -- model management --------------------------------------------------
    def pull(self, model: str, on_progress=None, cancel=None) -> PullProgress:
        """Download any model from the Ollama registry.

        Not restricted to a curated list: whatever name Ollama accepts, this
        accepts.  Downloading is the one operation in LocalFlow that needs the
        internet; everything afterwards runs offline.
        """
        name = validate_model_name(model)
        last = PullProgress(status="starting")
        layers: dict[str, tuple[int, int]] = {}
        try:
            # No read timeout: a multi-gigabyte pull legitimately takes minutes.
            with self._http().stream(
                "POST",
                "/api/pull",
                json={"model": name, "stream": True},
                timeout=httpx.Timeout(None, connect=10.0),
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if cancel is not None and cancel():
                        raise LlmError("llm_pull_cancelled", "Download cancelled.")
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                    except ValueError:
                        continue
                    if chunk.get("error"):
                        raise LlmError("llm_pull_failed", str(chunk["error"]))

                    digest = chunk.get("digest", "")
                    completed = int(chunk.get("completed", 0) or 0)
                    total = int(chunk.get("total", 0) or 0)
                    if digest and total:
                        layers[digest] = (completed, total)
                    # Report aggregate progress across all layers, which is what
                    # a person watching a download actually wants to see.
                    agg_completed = sum(c for c, _ in layers.values()) or completed
                    agg_total = sum(t for _, t in layers.values()) or total
                    last = PullProgress(
                        status=str(chunk.get("status", "")),
                        completed=agg_completed,
                        total=agg_total,
                        digest=digest,
                        done=str(chunk.get("status", "")).lower() == "success",
                    )
                    if on_progress is not None:
                        on_progress(last)
        except LlmError:
            raise
        except httpx.ConnectError as exc:
            raise LlmError(
                "llm_unreachable", "Ollama isn't running. Start Ollama and try again.", str(exc)
            ) from exc
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:400]
            if exc.response.status_code in (404, 400):
                raise LlmError(
                    "llm_model_not_found",
                    f"Ollama could not find a model called '{name}'. Check the name at "
                    "ollama.com/library.",
                    detail,
                ) from exc
            raise LlmError("llm_pull_failed", "The download failed.", detail) from exc
        except httpx.HTTPError as exc:
            raise LlmError(
                "llm_pull_failed",
                "The download was interrupted. Check your internet connection and retry.",
                str(exc),
            ) from exc

        self._models_cache = None
        if not last.done:
            last = PullProgress(status="success", completed=last.completed,
                                total=last.total, done=True)
        return last

    def delete(self, model: str) -> bool:
        name = validate_model_name(model)
        try:
            response = self._http().request(
                "DELETE", "/api/delete", json={"model": name}, timeout=30.0
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise LlmError(
                    "llm_model_missing", f"'{name}' is not installed."
                ) from exc
            raise LlmError("llm_delete_failed", "Could not delete the model.",
                           exc.response.text[:300]) from exc
        except httpx.HTTPError as exc:
            raise LlmError("llm_delete_failed", "Could not reach Ollama.", str(exc)) from exc
        self._models_cache = None
        return True

    def show(self, model: str) -> dict[str, Any]:
        """Metadata for a model, installed or not yet pulled."""
        name = validate_model_name(model)
        try:
            response = self._http().post("/api/show", json={"model": name}, timeout=15.0)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return {"installed": False, "name": name}
            raise LlmError("llm_failed", "Could not read model details.",
                           exc.response.text[:300]) from exc
        except httpx.HTTPError as exc:
            raise LlmError("llm_unreachable", "Could not reach Ollama.", str(exc)) from exc
        details = data.get("details") or {}
        info = data.get("model_info") or {}
        return {
            "installed": True,
            "name": name,
            "family": details.get("family", ""),
            "parameter_size": details.get("parameter_size", ""),
            "quantization": details.get("quantization_level", ""),
            "context_length": next(
                (v for k, v in info.items() if k.endswith("context_length")), None
            ),
            "capabilities": data.get("capabilities", []),
        }

    def running(self) -> list[dict[str, Any]]:
        """Models currently resident in Ollama's memory, with VRAM usage."""
        try:
            response = self._http().get("/api/ps", timeout=3.0)
            response.raise_for_status()
            return response.json().get("models", [])
        except Exception:
            return []


_CHAT_PREFIX = re.compile(
    r"^\s*(?:sure|certainly|of course|okay|ok|here(?:'s| is)|i(?:'ll| will| have| can)|"
    r"as an ai|absolutely|got it|understood|no problem)\b[^\n]{0,80}?[:,-]\s*",
    re.IGNORECASE,
)
_FENCE = re.compile(r"^```[a-zA-Z0-9_-]*\n(.*)\n```$", re.DOTALL)


_THINK_BLOCK = re.compile(r"<(think|thought|thinking|reasoning)>.*?</\1>", re.DOTALL | re.IGNORECASE)
_UNCLOSED_THINK = re.compile(r"^\s*<(?:think|thought|thinking|reasoning)>.*", re.DOTALL | re.IGNORECASE)


def clean_model_output(text: str) -> str:
    """Strip the wrappers chat-tuned models add even when told not to."""
    if not text:
        return ""
    out = text.strip()
    # Reasoning models (qwen3, deepseek-r1, ...) emit a thinking block first.
    # It is never part of the answer, and a truncated one leaves nothing usable.
    out = _THINK_BLOCK.sub("", out).strip()
    if _UNCLOSED_THINK.match(out):
        return ""
    fence = _FENCE.match(out)
    if fence:
        out = fence.group(1).strip()
    out = re.sub(r"</?(?:output|text|result|answer)>", "", out).strip()
    # Only remove a chat preamble when a newline separates it from real content,
    # or when the whole string starts with one.
    stripped = _CHAT_PREFIX.sub("", out, count=1)
    if stripped and stripped != out and len(stripped) > 8:
        out = stripped
    if len(out) >= 2 and out[0] in "\"'“" and out[-1] in "\"'”":
        inner = out[1:-1]
        if inner.count('"') == 0 and inner.count("“") == 0:
            out = inner
    return out.strip()


# Models worth suggesting during onboarding when nothing suitable is installed.
# Sized so they coexist with Whisper on a 6 GB laptop GPU.
SUGGESTED_MODELS: tuple[dict[str, object], ...] = (
    {
        "name": "qwen2.5:7b-instruct",
        "size_gb": 4.7,
        "note": (
            "Most accurate. Best at keeping other languages untranslated and at "
            "never answering what you dictated. Shares a 6 GB GPU with Whisper by "
            "offloading part of itself to the CPU, which adds about a second."
        ),
        "recommended": True,
    },
    {
        "name": "qwen2.5:3b-instruct",
        "size_gb": 1.9,
        "note": "Fastest good option - roughly 600 ms. Fits beside Whisper on 6 GB.",
        "recommended": False,
    },
    {
        "name": "gemma3:4b-it-q4_K_M",
        "size_gb": 3.3,
        "note": "Middle ground on both speed and accuracy.",
        "recommended": False,
    },
    {
        "name": "qwen2.5:1.5b-instruct",
        "size_gb": 1.0,
        "note": "For CPU-only machines. Noticeably weaker at following the editing rules.",
        "recommended": False,
    },
)

# Measured on an RTX 3060 6 GB with large-v3-turbo already resident
# (benchmarks/compare_models.py --llm). Pass rate is out of 9 editing cases.
MODEL_BENCHMARKS: dict[str, dict[str, float | int]] = {
    "qwen2.5:7b-instruct": {"passed": 7, "median_ms": 1590, "vram_mb": 4303},
    "qwen2.5:3b-instruct": {"passed": 6, "median_ms": 640, "vram_mb": 2935},
    "gemma3:4b-it-q4_K_M": {"passed": 6, "median_ms": 596, "vram_mb": 3659},
    "llama3.1:8b": {"passed": 4, "median_ms": 1131, "vram_mb": 4379},
}

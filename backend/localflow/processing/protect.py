"""Shield literals that must survive the text stages byte-for-byte.

Sentence-casing, spacing repair and tokenisation all assume prose. Applied to
`https://example.com/api/v2`, `shipment_service.py` or `getShipmentStatus`, they
produce garbage ("The docs are at https: / / example. Com").

So those spans are swapped for opaque placeholders before the prose stages run
and restored afterwards. The placeholder is a bare uppercase word so that every
stage treats it as one ordinary token: tokenisers keep it whole, the capitaliser
leaves it alone, and filler removal never matches it.
"""
from __future__ import annotations

import re

_PLACEHOLDER = "LFZ{index}ZQ"
_PLACEHOLDER_RE = re.compile(r"LFZ(\d+)ZQ")

# Ordered: the first pattern to match a span wins, so URLs beat bare filenames.
PATTERNS: tuple[re.Pattern[str], ...] = (
    # URLs and bare hostnames with a path.
    re.compile(r"\b(?:https?|ftp|file)://[^\s<>\"']+", re.IGNORECASE),
    re.compile(r"\bwww\.[^\s<>\"']+", re.IGNORECASE),
    # Email addresses.
    re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"),
    # Windows and UNC paths.
    re.compile(r"\b[A-Za-z]:\\[^\s\"'<>|]+"),
    re.compile(r"\\\\[^\s\"'<>|]+"),
    # POSIX-style paths with at least two segments.
    re.compile(r"(?:\.{0,2}/)[\w.-]+(?:/[\w.-]+)+/?"),
    # Filenames with a known code/data extension.
    re.compile(
        r"\b[\w-]+\.(?:py|pyi|js|mjs|cjs|ts|tsx|jsx|rs|go|java|kt|cs|cpp|cc|hpp|h|rb|php|swift"
        r"|sql|sh|bash|ps1|bat|cmd|toml|yaml|yml|json|jsonl|xml|html|htm|css|scss|md|txt|csv"
        r"|tsv|ini|cfg|conf|env|lock|log|exe|dll|so|dylib|zip|tar|gz|pdf|png|jpg|jpeg|gif|svg"
        r"|wav|mp3|mp4|onnx|gguf|safetensors)\b",
        re.IGNORECASE,
    ),
    # snake_case and SCREAMING_SNAKE_CASE identifiers.
    re.compile(r"\b[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+\b"),
    # kebab-case package names (npm/cargo style), two or more segments.
    re.compile(r"\b[a-z][a-z0-9]*(?:-[a-z0-9]+){1,}\b(?=\s|$|[.,;:!?)])"),
    # camelCase and PascalCase identifiers.
    re.compile(r"\b[a-z]+[A-Z][A-Za-z0-9]*\b"),
    re.compile(r"\b[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+\b"),
    # Dotted module / namespace references.
    re.compile(r"\b[A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*){1,}\b(?<!\.)"),
    # Version strings and semantic versions.
    re.compile(r"\bv?\d+\.\d+(?:\.\d+)*(?:-[\w.]+)?\b"),
    # Command-line flags.
    re.compile(r"(?<!\w)--?[A-Za-z][\w-]*\b"),
    # Anything the speaker already put in backticks.
    re.compile(r"`[^`\n]{1,200}`"),
)

# Ordinary prose that the identifier patterns would otherwise capture.
_ALLOWLIST = {
    "e.g", "i.e", "etc", "vs", "a.m", "p.m", "u.s", "u.k", "ph.d", "mr", "mrs", "ms", "dr",
}


def _is_real_prose(match: str) -> bool:
    lowered = match.lower().strip(".")
    if lowered in _ALLOWLIST:
        return True
    # "Mr. Smith" style: a dotted pair whose first half is an abbreviation.
    head = lowered.split(".")[0]
    return head in _ALLOWLIST


def mask(text: str) -> tuple[str, list[str]]:
    """Replace protected spans with placeholders.

    Returns the masked text and the ordered list of originals.
    """
    if not text:
        return text, []
    tokens: list[str] = []
    spans: list[tuple[int, int, str]] = []

    for pattern in PATTERNS:
        for match in pattern.finditer(text):
            start, end = match.span()
            if any(start < e and s < end for s, e, _ in spans):
                continue  # already covered by an earlier, higher-priority rule
            value = match.group(0)
            if len(value) < 3 or _is_real_prose(value):
                continue
            spans.append((start, end, value))

    if not spans:
        return text, []

    spans.sort(key=lambda item: item[0])
    out: list[str] = []
    cursor = 0
    for start, end, value in spans:
        out.append(text[cursor:start])
        out.append(_PLACEHOLDER.format(index=len(tokens)))
        tokens.append(value)
        cursor = end
    out.append(text[cursor:])
    return "".join(out), tokens


def unmask(text: str, tokens: list[str]) -> str:
    """Put the originals back."""
    if not tokens or not text:
        return text

    def restore(match: re.Match) -> str:
        index = int(match.group(1))
        return tokens[index] if 0 <= index < len(tokens) else match.group(0)

    return _PLACEHOLDER_RE.sub(restore, text)


def contains_placeholder(text: str) -> bool:
    return bool(_PLACEHOLDER_RE.search(text))


def protected_terms(tokens: list[str]) -> list[str]:
    """The literals, for the validation stage to check survived the LLM."""
    return list(dict.fromkeys(tokens))

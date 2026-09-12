"""Generate LocalFlow's application and tray icons.

Rendered procedurally (numpy + a small PNG writer) so the repository carries no
binary blobs and the marks stay editable.  Everything is drawn at 4x and
box-filtered down, which is what keeps the 16 px tray icon legible.

    python scripts/make_icons.py
"""
from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
ICON_DIR = ROOT / "desktop" / "src-tauri" / "icons"
SS = 4  # supersampling factor

# Brand palette.
INK = (14, 16, 22)
SURFACE_TOP = (61, 78, 168)
SURFACE_BOTTOM = (32, 36, 74)
ACCENT = (126, 156, 255)
WHITE = (255, 255, 255)
RECORD = (255, 92, 92)
AMBER = (245, 186, 96)


def _canvas(size: int) -> np.ndarray:
    return np.zeros((size, size, 4), dtype=np.float64)


def _rounded_rect_mask(size: int, radius: float, inset: float = 0.0) -> np.ndarray:
    """Signed-coverage mask for a rounded rectangle."""
    ys, xs = np.mgrid[0:size, 0:size].astype(np.float64)
    xs += 0.5
    ys += 0.5
    lo = inset
    hi = size - inset
    r = min(radius, (hi - lo) / 2)
    cx = np.clip(xs, lo + r, hi - r)
    cy = np.clip(ys, lo + r, hi - r)
    dist = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2)
    inside = (xs >= lo) & (xs <= hi) & (ys >= lo) & (ys <= hi)
    mask = np.where(dist <= r, 1.0, 0.0)
    return np.where(inside | (dist <= r), mask, 0.0)


def _vertical_gradient(size: int, top: tuple, bottom: tuple) -> np.ndarray:
    t = np.linspace(0.0, 1.0, size).reshape(-1, 1)
    out = np.zeros((size, size, 3), dtype=np.float64)
    for i in range(3):
        out[:, :, i] = top[i] * (1 - t) + bottom[i] * t
    return out


def _composite(canvas: np.ndarray, mask: np.ndarray, color: tuple, alpha: float = 1.0) -> None:
    a = np.clip(mask * alpha, 0.0, 1.0)
    rgb = np.array(color, dtype=np.float64).reshape(1, 1, 3)
    existing = canvas[:, :, 3:4]
    out_a = a[:, :, None] + existing * (1 - a[:, :, None])
    safe = np.where(out_a > 0, out_a, 1.0)
    canvas[:, :, :3] = (
        rgb * a[:, :, None] + canvas[:, :, :3] * existing * (1 - a[:, :, None])
    ) / safe
    canvas[:, :, 3:4] = out_a


def _composite_rgb(canvas: np.ndarray, mask: np.ndarray, rgb: np.ndarray) -> None:
    a = np.clip(mask, 0.0, 1.0)[:, :, None]
    existing = canvas[:, :, 3:4]
    out_a = a + existing * (1 - a)
    safe = np.where(out_a > 0, out_a, 1.0)
    canvas[:, :, :3] = (rgb * a + canvas[:, :, :3] * existing * (1 - a)) / safe
    canvas[:, :, 3:4] = out_a


def _bar(size: int, cx: float, half_height: float, width: float) -> np.ndarray:
    radius = width / 2
    ys, xs = np.mgrid[0:size, 0:size].astype(np.float64)
    xs += 0.5
    ys += 0.5
    cy = size / 2
    top = cy - half_height + radius
    bottom = cy + half_height - radius
    qy = np.clip(ys, min(top, bottom), max(top, bottom))
    dist = np.sqrt((xs - cx) ** 2 + (ys - qy) ** 2)
    return np.clip(radius + 0.5 - dist, 0.0, 1.0)


def _downsample(canvas: np.ndarray, factor: int) -> np.ndarray:
    size = canvas.shape[0] // factor
    reshaped = canvas.reshape(size, factor, size, factor, 4)
    return reshaped.mean(axis=(1, 3))


def _to_bytes(canvas: np.ndarray) -> np.ndarray:
    """RGB is carried at 0-255 but coverage is carried at 0-1; scale on the way out."""
    out = canvas.copy()
    out[:, :, 3] *= 255.0
    return np.clip(np.round(out), 0, 255).astype(np.uint8)


# -- the marks ------------------------------------------------------------
WAVE = (0.36, 0.66, 1.0, 0.72, 0.44)


def app_icon(size: int) -> np.ndarray:
    s = size * SS
    canvas = _canvas(s)
    body = _rounded_rect_mask(s, radius=s * 0.225, inset=s * 0.045)
    _composite_rgb(canvas, body, _vertical_gradient(s, SURFACE_TOP, SURFACE_BOTTOM))

    # A soft top highlight gives the tile depth without a glossy gradient.
    highlight = _rounded_rect_mask(s, radius=s * 0.225, inset=s * 0.045)
    fade = np.clip(1.0 - np.linspace(0, 1, s) * 2.4, 0, 1).reshape(-1, 1)
    _composite(canvas, highlight * fade, WHITE, alpha=0.10)

    width = s * 0.082
    gap = s * 0.155
    start = s / 2 - gap * 2
    for i, scale in enumerate(WAVE):
        cx = start + gap * i
        _composite(canvas, _bar(s, cx, s * 0.30 * scale, width), WHITE, alpha=0.97)
    return _to_bytes(_downsample(canvas, SS))


def tray_icon(size: int, variant: str) -> np.ndarray:
    """Monochrome-ish tray marks that stay readable at 16 px."""
    s = size * SS
    canvas = _canvas(s)
    width = s * 0.105
    gap = s * 0.185
    start = s / 2 - gap * 2

    if variant == "paused":
        colour, alpha, wave = WHITE, 0.45, (0.30, 0.30, 0.30, 0.30, 0.30)
    elif variant == "recording":
        colour, alpha, wave = RECORD, 1.0, (0.42, 0.78, 1.0, 0.82, 0.50)
    elif variant == "processing":
        colour, alpha, wave = AMBER, 1.0, (0.30, 0.55, 0.85, 0.55, 0.30)
    else:
        colour, alpha, wave = WHITE, 0.95, WAVE

    for i, scale in enumerate(wave):
        cx = start + gap * i
        _composite(canvas, _bar(s, cx, s * 0.36 * scale, width), colour, alpha=alpha)

    if variant == "paused":
        for offset in (-s * 0.075, s * 0.075):
            _composite(canvas, _bar(s, s / 2 + offset, s * 0.20, s * 0.10), WHITE, alpha=0.85)
    return _to_bytes(_downsample(canvas, SS))


# -- encoders -------------------------------------------------------------
def _chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def encode_png(rgba: np.ndarray) -> bytes:
    height, width, _ = rgba.shape
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter type 0
        raw.extend(rgba[y].tobytes())
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + _chunk(b"IEND", b"")
    )


def encode_ico(images: list[np.ndarray]) -> bytes:
    """ICO with embedded PNGs (supported since Windows Vista)."""
    payloads = [encode_png(img) for img in images]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = bytearray()
    for img, payload in zip(images, payloads):
        size = img.shape[0]
        entries += struct.pack(
            "<BBBBHHII", 0 if size >= 256 else size, 0 if size >= 256 else size,
            0, 0, 1, 32, len(payload), offset
        )
        offset += len(payload)
    return header + bytes(entries) + b"".join(payloads)


def main() -> int:
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    written: list[str] = []

    for size, name in (
        (32, "32x32.png"),
        (128, "128x128.png"),
        (256, "128x128@2x.png"),
        (512, "icon.png"),
        (30, "Square30x30Logo.png"),
        (44, "Square44x44Logo.png"),
        (71, "Square71x71Logo.png"),
        (89, "Square89x89Logo.png"),
        (107, "Square107x107Logo.png"),
        (142, "Square142x142Logo.png"),
        (150, "Square150x150Logo.png"),
        (284, "Square284x284Logo.png"),
        (310, "Square310x310Logo.png"),
        (50, "StoreLogo.png"),
    ):
        (ICON_DIR / name).write_bytes(encode_png(app_icon(size)))
        written.append(name)

    (ICON_DIR / "icon.ico").write_bytes(
        encode_ico([app_icon(s) for s in (16, 32, 48, 64, 128, 256)])
    )
    written.append("icon.ico")

    for variant in ("active", "paused", "recording", "processing"):
        name = f"tray-{variant}.png"
        (ICON_DIR / name).write_bytes(encode_png(tray_icon(32, variant)))
        written.append(name)

    print(f"Wrote {len(written)} icons to {ICON_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

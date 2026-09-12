"""Microphone enumeration."""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


class AudioUnavailable(RuntimeError):
    """Raised when no usable audio backend is present."""


def _sd():
    try:
        import sounddevice as sd  # imported lazily: PortAudio init is not free
    except Exception as exc:  # pragma: no cover - environment dependent
        raise AudioUnavailable(
            "Audio backend unavailable. Reinstall the LocalFlow Python "
            "environment (sounddevice/PortAudio failed to load)."
        ) from exc
    return sd


# Windows exposes the same physical microphone through several host APIs.
# WASAPI is the modern shared-mode path and is by far the most reliable;
# WDM-KS is a kernel-streaming path that opens the device *exclusively* and
# fails with "Unanticipated host error [PaErrorCode -9999]" whenever anything
# else already holds it - which on a laptop is most of the time.
_HOST_API_RANK = {
    "windows wasapi": 0,
    "windows directsound": 1,
    "mme": 2,
    "windows wdm-ks": 9,
}
_AVOID_UNLESS_CHOSEN = {"windows wdm-ks"}

# Names Windows exposes that are not physical devices. "Primary Sound Capture
# Driver" and "Microsoft Sound Mapper" are routers: they record from whatever
# Windows currently considers the default, which means opening one always
# succeeds and may still deliver nothing. They are a last resort, never a
# silent fallback.
_ALIAS_NAMES = ("sound mapper", "primary sound capture", "primary sound driver")


def is_alias(name: str) -> bool:
    lowered = (name or "").lower()
    return any(alias in lowered for alias in _ALIAS_NAMES)


def refresh() -> None:
    """Re-enumerate audio hardware.

    PortAudio snapshots the device list when it initialises and never updates
    it, so a headset plugged in after LocalFlow started is invisible - and the
    cached indices can point at endpoints Windows has since renumbered, which
    is why an open of a device that plainly exists can fail. Every stream must
    be closed first; re-initialising underneath a live stream faults.
    """
    sd = _sd()
    try:
        sd._terminate()
        sd._initialize()
        log.info("Re-enumerated audio devices")
    except Exception:  # pragma: no cover - depends on the PortAudio build
        log.warning("Could not re-enumerate audio devices", exc_info=True)


def host_api_rank(host_api: str) -> int:
    return _HOST_API_RANK.get((host_api or "").strip().lower(), 5)


def list_input_devices() -> list[dict[str, Any]]:
    """All input-capable devices, default first in the ``is_default`` flag."""
    sd = _sd()
    try:
        devices = sd.query_devices()
        default_in = sd.default.device[0] if sd.default.device else None
    except Exception as exc:
        raise AudioUnavailable(str(exc)) from exc

    out: list[dict[str, Any]] = []
    for idx, dev in enumerate(devices):
        if int(dev.get("max_input_channels", 0)) <= 0:
            continue
        try:
            host = sd.query_hostapis(dev["hostapi"])["name"]
        except Exception:
            host = ""
        out.append(
            {
                "id": idx,
                "name": dev.get("name", f"Device {idx}"),
                "host_api": host,
                "channels": int(dev.get("max_input_channels", 1)),
                "default_samplerate": int(dev.get("default_samplerate", 44100) or 44100),
                "is_default": idx == default_in,
                "rank": host_api_rank(host),
                "exclusive": host.strip().lower() in _AVOID_UNLESS_CHOSEN,
                "alias": is_alias(dev.get("name", "")),
            }
        )
    return out


def resolve_device(device_id: int | None, device_name: str = "") -> int | None:
    """Map stored settings onto a currently-present device index.

    Device indices are not stable across reboots or USB re-plugs, so the stored
    name is the tiebreaker: it survives re-enumeration where the index does not.

    Where a name matches several host APIs - the usual case on Windows, which
    lists one microphone four times - the best-ranked one wins, so a fuzzy name
    match can never silently land on an exclusive-mode WDM-KS device.
    """
    try:
        devices = list_input_devices()
    except AudioUnavailable:
        return device_id
    if not devices:
        return None

    # An explicit index the user picked is honoured as-is, exclusive or not.
    by_id = {d["id"]: d for d in devices}
    if device_id is not None and device_id in by_id:
        if not device_name or by_id[device_id]["name"] == device_name:
            return device_id

    if device_name:
        needle = device_name.strip().lower()
        exact = [d for d in devices if d["name"].strip().lower() == needle]
        partial = [d for d in devices if _same_device(d["name"], device_name)]
        for group in (exact, partial):
            usable = [d for d in group if not d["exclusive"]] or group
            if usable:
                return min(usable, key=lambda d: (d["rank"], d["id"]))["id"]

    return default_device_id(devices)


def _same_device(a: str, b: str) -> bool:
    """Whether two host APIs are describing the same physical microphone."""
    left, right = a.strip().lower(), b.strip().lower()
    if left == right:
        return True
    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    # MME's 31-character cap means a prefix match is the only reliable test.
    return len(shorter) >= 12 and longer.startswith(shorter)


def default_device_id(devices: list[dict[str, Any]] | None = None) -> int | None:
    """The best device to record from when the user has not chosen one."""
    devices = devices if devices is not None else list_input_devices()
    if not devices:
        return None
    usable = [d for d in devices if not d["exclusive"]] or devices

    # Prefer the system default, but on its best-ranked host API rather than
    # whichever index PortAudio happened to report.
    default = next((d for d in devices if d["is_default"]), None)
    if default is not None:
        # MME truncates device names to 31 characters, so the same microphone
        # reads as "Microphone Array (Realtek(R) Au" there and
        # "Microphone Array (Realtek(R) Audio)" on WASAPI. Compare on the
        # shorter of the two names, or they never match.
        same_name = [d for d in usable if _same_device(d["name"], default["name"])]
        if same_name:
            return min(same_name, key=lambda d: (d["rank"], d["id"]))["id"]
        if not default["exclusive"]:
            return default["id"]

    # "Sound Mapper" and "Primary Sound Capture Driver" are aliases, not real
    # devices; a named endpoint behaves far better.
    named = [d for d in usable if not d["alias"]] or usable
    return min(named, key=lambda d: (d["rank"], d["id"]))["id"]


def device_info(device_id: int | None) -> dict[str, Any] | None:
    if device_id is None:
        return None
    for dev in list_input_devices():
        if dev["id"] == device_id:
            return dev
    return None


def deduplicate_devices(devices: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """One entry per physical microphone, for the device picker.

    Windows enumerates the same microphone once per host API, so a laptop with
    two inputs shows up as fifteen entries - most of them duplicates, plus
    router aliases and exclusive-mode kernel-streaming endpoints that will fail
    to open. Presenting that list asks the user to make a decision they have no
    basis for making, so the UI gets the collapsed list and the raw one stays
    available behind a toggle.
    """
    devices = devices if devices is not None else list_input_devices()

    groups: list[list[dict[str, Any]]] = []
    for device in sorted(devices, key=lambda d: (d["rank"], d["id"])):
        if device["alias"]:
            continue
        for group in groups:
            if _same_device(group[0]["name"], device["name"]):
                group.append(device)
                break
        else:
            groups.append([device])

    out: list[dict[str, Any]] = []
    for group in groups:
        best = min(group, key=lambda d: (d["rank"], d["id"]))
        if best["exclusive"]:
            continue  # nothing in this group can be opened reliably
        # MME truncates names at 31 characters; show the fullest one we saw.
        label = max((d["name"] for d in group), key=len)
        out.append(
            {
                **best,
                "name": label,
                "is_default": any(d["is_default"] for d in group),
                "aliases": sorted({d["id"] for d in group} - {best["id"]}),
            }
        )

    out.sort(key=lambda d: (not d["is_default"], d["rank"], d["name"].lower()))
    return out


def candidates_for(device_id: int | None, device_name: str = "") -> list[dict[str, Any]]:
    """Microphones to try, best first.

    Falling back matters when a device will not open, but *what* we fall back
    to matters more. Dropping from the user's headset onto "Primary Sound
    Capture Driver" looks like success and records silence, which is the worst
    possible outcome: the meter sits at zero and nothing explains why. So the
    order is: the requested device, then the same physical microphone on a
    different driver (a Realtek jack that fails on WASAPI usually still opens
    on DirectSound, and it is the same audio either way), then other real
    microphones, and only then the routers.
    """
    try:
        devices = list_input_devices()
    except AudioUnavailable:
        return []
    if not devices:
        return []

    by_id = {d["id"]: d for d in devices}
    ordered: list[dict[str, Any]] = []
    seen: set[int] = set()

    def push(device: dict[str, Any]) -> None:
        if device["id"] not in seen:
            seen.add(device["id"])
            ordered.append(device)

    resolved = resolve_device(device_id, device_name)
    chosen = by_id.get(resolved) if resolved is not None else None
    if chosen is not None:
        push(chosen)

    target = (chosen or {}).get("name") or device_name
    if target:
        siblings = [
            d for d in devices if not d["exclusive"] and _same_device(d["name"], target)
        ]
        for device in sorted(siblings, key=lambda d: (d["rank"], d["id"])):
            push(device)

    others = [d for d in devices if not d["exclusive"] and not d["alias"]]
    for device in sorted(others, key=lambda d: (not d["is_default"], d["rank"], d["id"])):
        push(device)

    routers = [d for d in devices if not d["exclusive"] and d["alias"]]
    for device in sorted(routers, key=lambda d: (d["rank"], d["id"])):
        push(device)

    return ordered

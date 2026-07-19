#!/usr/bin/env python3
"""Detect host CPU / cache info for memory-mountain plot titles (Linux, macOS, Windows)."""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output" / "host_info.json"


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def _fmt_bytes(n: int | None) -> str | None:
    if n is None or n <= 0:
        return None
    if n >= 1 << 30:
        return f"{n / (1 << 30):.1f} GiB"
    if n >= 1 << 20:
        v = n / (1 << 20)
        return f"{int(v)} MiB" if abs(v - int(v)) < 1e-9 else f"{v:.1f} MiB"
    if n >= 1 << 10:
        v = n / (1 << 10)
        return f"{int(v)} KiB" if abs(v - int(v)) < 1e-9 else f"{v:.1f} KiB"
    return f"{n} B"


def detect_macos() -> dict:
    info: dict = {"os": "macOS", "cpu": _run(["sysctl", "-n", "machdep.cpu.brand_string"]) or "Apple"}
    caches = {}
    for key, label in [
        ("hw.cachelinesize", "line"),
        ("hw.l1dcachesize", "L1d"),
        ("hw.l2cachesize", "L2"),
        ("hw.l3cachesize", "L3"),
    ]:
        raw = _run(["sysctl", "-n", key])
        if raw.isdigit():
            caches[label] = int(raw)

    # Prefer performance-core sizes when present (Apple Silicon).
    pl0_l1 = _run(["sysctl", "-n", "hw.perflevel0.l1dcachesize"])
    pl0_l2 = _run(["sysctl", "-n", "hw.perflevel0.l2cachesize"])
    pl1_l1 = _run(["sysctl", "-n", "hw.perflevel1.l1dcachesize"])
    pl1_l2 = _run(["sysctl", "-n", "hw.perflevel1.l2cachesize"])
    if pl0_l1.isdigit():
        caches["L1d_P"] = int(pl0_l1)
    if pl0_l2.isdigit():
        caches["L2_P"] = int(pl0_l2)
    if pl1_l1.isdigit():
        caches["L1d_E"] = int(pl1_l1)
    if pl1_l2.isdigit():
        caches["L2_E"] = int(pl1_l2)

    info["caches"] = caches
    return info


def detect_linux() -> dict:
    info: dict = {"os": "Linux", "cpu": ""}
    try:
        cpuinfo = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"^model name\s*:\s*(.+)$", cpuinfo, re.M)
        if m:
            info["cpu"] = m.group(1).strip()
        else:
            m = re.search(r"^Hardware\s*:\s*(.+)$", cpuinfo, re.M)
            info["cpu"] = m.group(1).strip() if m else platform.processor() or "CPU"
    except OSError:
        info["cpu"] = platform.processor() or "CPU"

    caches: dict[str, int] = {}
    base = Path("/sys/devices/system/cpu/cpu0/cache")
    if base.is_dir():
        for index in sorted(base.glob("index*")):
            try:
                level = (index / "level").read_text().strip()
                size_s = (index / "size").read_text().strip()  # e.g. 32K
                typ = (index / "type").read_text().strip()
                co = (index / "coherency_line_size").read_text().strip()
            except OSError:
                continue
            mult = 1
            if size_s.endswith("K"):
                mult = 1024
                size_s = size_s[:-1]
            elif size_s.endswith("M"):
                mult = 1024 * 1024
                size_s = size_s[:-1]
            try:
                nbytes = int(size_s) * mult
            except ValueError:
                continue
            if typ.lower() == "data" or typ.lower() == "unified":
                key = f"L{level}" + ("d" if typ.lower() == "data" and level == "1" else "")
                if level == "1" and typ.lower() == "data":
                    key = "L1d"
                elif level == "2":
                    key = "L2"
                elif level == "3":
                    key = "L3"
                caches[key] = nbytes
            if co.isdigit() and "line" not in caches:
                caches["line"] = int(co)
    info["caches"] = caches
    return info


def _windows_caches_glpi() -> dict[str, int]:
    """Read L1d/L2/L3 via GetLogicalProcessorInformation (same source as mountain.cpp)."""
    if sys.platform != "win32":
        return {}
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return {}

    class CACHE_DESCRIPTOR(ctypes.Structure):
        _fields_ = [
            ("Level", ctypes.c_ubyte),
            ("Associativity", ctypes.c_ubyte),
            ("LineSize", ctypes.c_ushort),
            ("Size", ctypes.c_ulong),
            ("Type", ctypes.c_int),
        ]

    class _SLPI_UNION(ctypes.Union):
        _fields_ = [
            ("Cache", CACHE_DESCRIPTOR),
            ("Reserved", ctypes.c_ulonglong * 2),
        ]

    ulong_ptr = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

    class SYSTEM_LOGICAL_PROCESSOR_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("ProcessorMask", ulong_ptr),
            ("Relationship", ctypes.c_int),
            ("u", _SLPI_UNION),
        ]

    RelationCache = 2
    CacheUnified = 0
    CacheData = 2
    ERROR_INSUFFICIENT_BUFFER = 122

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    glpi = kernel32.GetLogicalProcessorInformation
    glpi.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
    glpi.restype = wintypes.BOOL

    needed = wintypes.DWORD(0)
    if glpi(None, ctypes.byref(needed)):
        return {}
    if ctypes.get_last_error() != ERROR_INSUFFICIENT_BUFFER or needed.value == 0:
        return {}

    entry = SYSTEM_LOGICAL_PROCESSOR_INFORMATION
    entry_size = ctypes.sizeof(entry)
    count = max(1, (needed.value + entry_size - 1) // entry_size)
    arr_t = entry * count
    arr = arr_t()
    buf_bytes = wintypes.DWORD(count * entry_size)
    if not glpi(ctypes.byref(arr), ctypes.byref(buf_bytes)):
        return {}

    n = buf_bytes.value // entry_size
    caches: dict[str, int] = {}
    for i in range(n):
        item = arr[i]
        if item.Relationship != RelationCache:
            continue
        cache = item.u.Cache
        sz = int(cache.Size)
        if sz <= 0:
            continue
        if int(cache.LineSize) > 0:
            caches["line"] = int(cache.LineSize)
        if int(cache.Type) not in (CacheData, CacheUnified):
            continue
        level = int(cache.Level)
        if level == 1:
            caches["L1d"] = max(caches.get("L1d", 0), sz)
        elif level == 2:
            caches["L2"] = max(caches.get("L2", 0), sz)
        elif level == 3:
            caches["L3"] = max(caches.get("L3", 0), sz)
    return {k: v for k, v in caches.items() if v > 0}


def detect_windows() -> dict:
    info: dict = {"os": "Windows", "cpu": platform.processor() or "CPU", "caches": {}}
    # Best-effort via PowerShell if available.
    out = _run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "(Get-CimInstance Win32_Processor).Name",
        ]
    )
    if out:
        info["cpu"] = out.splitlines()[0].strip()

    caches = _windows_caches_glpi()
    if caches:
        info["caches"] = caches
    return info


def detect() -> dict:
    system = platform.system()
    if system == "Darwin":
        info = detect_macos()
    elif system == "Linux":
        info = detect_linux()
    elif system == "Windows":
        info = detect_windows()
    else:
        info = {"os": system, "cpu": platform.processor() or "CPU", "caches": {}}
    info["arch"] = platform.machine()
    info["platform"] = platform.platform()
    return info


def title_line(info: dict) -> str:
    cpu = info.get("cpu") or "CPU"
    caches = info.get("caches") or {}
    parts = [cpu]
    # Prefer P/E split on Apple Silicon; else classic L1d/L2/L3.
    if "L1d_P" in caches or "L2_P" in caches:
        bits = []
        if "L1d_P" in caches:
            bits.append(f"P L1d {_fmt_bytes(caches['L1d_P'])}")
        if "L2_P" in caches:
            bits.append(f"L2 {_fmt_bytes(caches['L2_P'])}")
        if "L1d_E" in caches:
            bits.append(f"E L1d {_fmt_bytes(caches['L1d_E'])}")
        if "L2_E" in caches:
            bits.append(f"L2 {_fmt_bytes(caches['L2_E'])}")
        if "line" in caches:
            bits.append(f"line {_fmt_bytes(caches['line'])}")
        if bits:
            parts.append(" — " + "; ".join(bits))
    else:
        bits = []
        for k in ("L1d", "L2", "L3", "line"):
            if k in caches:
                bits.append(f"{k} {_fmt_bytes(caches[k])}")
        if bits:
            parts.append(" — " + ", ".join(bits))
    return "".join(parts)


def main() -> int:
    info = detect()
    info["title"] = title_line(info)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    print(info["title"])
    print(f"wrote {OUT}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Plot a CSAPP-style memory mountain from mountain.csv (any host)."""

from __future__ import annotations

from pathlib import Path
import argparse
import csv
import json

import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parent
DEFAULT_CSV = ROOT / "output" / "mountain.csv"
DEFAULT_HOST = ROOT / "output" / "host_info.json"
DEFAULT_OUT = ROOT / "output" / "memory_mountain.png"


def load(path: Path):
    rows = list(csv.DictReader(path.open(newline="")))
    sizes = sorted({int(r["size_bytes"]) for r in rows})
    strides = sorted({int(r["stride_elems"]) for r in rows})
    z = np.zeros((len(strides), len(sizes)))
    lookup = {
        (int(r["size_bytes"]), int(r["stride_elems"])): float(r["throughput_MBps"])
        for r in rows
    }
    for i, s in enumerate(strides):
        for j, n in enumerate(sizes):
            z[i, j] = lookup.get((n, s), np.nan)
    elem_bytes = 8
    if rows and "stride_bytes" in rows[0] and "stride_elems" in rows[0]:
        se = int(rows[0]["stride_elems"])
        sb = int(rows[0]["stride_bytes"])
        if se > 0:
            elem_bytes = sb // se
    return sizes, strides, z, elem_bytes


def _fmt_size(n: int) -> str:
    if n >= 1 << 20:
        return f"{n // (1 << 20)}m"
    if n >= 1 << 10:
        return f"{n // (1 << 10)}k"
    return str(n)


def _fmt_bytes_label(n: int) -> str:
    if n >= 1 << 20:
        v = n / (1 << 20)
        return f"{int(v)} MiB" if abs(v - int(v)) < 1e-9 else f"{v:.1f} MiB"
    if n >= 1 << 10:
        v = n / (1 << 10)
        return f"{int(v)} KiB" if abs(v - int(v)) < 1e-9 else f"{v:.1f} KiB"
    return f"{n} B"


def load_cache_levels(host_path: Path, sweep_meta_path: Path | None = None) -> list[tuple[str, int]]:
    """Return ordered (label, bytes) cache levels for vertical markers."""
    caches: dict = {}
    if sweep_meta_path and sweep_meta_path.is_file():
        try:
            meta = json.loads(sweep_meta_path.read_text(encoding="utf-8"))
            caches = dict(meta.get("caches") or {})
        except (OSError, json.JSONDecodeError):
            caches = {}
    if not caches and host_path.is_file():
        try:
            info = json.loads(host_path.read_text(encoding="utf-8"))
            caches = dict(info.get("caches") or {})
        except (OSError, json.JSONDecodeError):
            caches = {}

    levels: list[tuple[str, int]] = []
    # Prefer P-core sizes on Apple Silicon when present.
    if "L1d_P" in caches or caches.get("L1d"):
        l1 = int(caches.get("L1d_P") or caches.get("L1d") or 0)
        if l1 > 0:
            levels.append(("L1d", l1))
    if "L2_P" in caches or caches.get("L2"):
        l2 = int(caches.get("L2_P") or caches.get("L2") or 0)
        if l2 > 0:
            levels.append(("L2", l2))
    l3 = int(caches.get("L3") or 0)
    if l3 >= (1 << 20):
        levels.append(("L3", l3))
    return levels


def nearest_size_index(sizes: list[int], target: int) -> int | None:
    if not sizes:
        return None
    return min(range(len(sizes)), key=lambda i: abs(sizes[i] - target))


def host_title(host_path: Path, csv_path: Path | None = None) -> str:
    title = "Memory mountain"
    if host_path.is_file():
        try:
            info = json.loads(host_path.read_text(encoding="utf-8"))
            title = info.get("title") or info.get("gpu") or info.get("cpu") or title
        except (OSError, json.JSONDecodeError):
            pass
    if csv_path and csv_path.is_file():
        try:
            rows = list(csv.DictReader(csv_path.open(newline="")))
            if rows and "dtype" in rows[0] and rows[0]["dtype"]:
                title = f"{title} [{rows[0]['dtype']}]"
            elif rows and "stride_bytes" in rows[0] and "stride_elems" in rows[0]:
                se = int(rows[0]["stride_elems"])
                sb = int(rows[0]["stride_bytes"])
                if se > 0 and sb // se == 4:
                    title = f"{title} [float]"
                elif se > 0 and sb // se == 8:
                    title = f"{title} [double]"
        except (OSError, ValueError, KeyError):
            pass
    return title


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    ap.add_argument("--host", type=Path, default=DEFAULT_HOST)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument(
        "--sweep-meta",
        type=Path,
        default=None,
        help="Optional sweep_meta.json from ./mountain (defaults beside --csv)",
    )
    args = ap.parse_args()

    sizes, strides, z, elem_bytes = load(args.csv)
    if not sizes or not strides:
        raise SystemExit(f"no samples in {args.csv}; run ./mountain first")

    sweep_meta = args.sweep_meta
    if sweep_meta is None:
        sweep_meta = args.csv.parent / "sweep_meta.json"
    cache_levels = load_cache_levels(args.host, sweep_meta)

    stride_label = f"Stride (x{elem_bytes} bytes)"
    x = np.arange(len(sizes))
    y = np.arange(len(strides))
    X, Y = np.meshgrid(x, y)

    # Dense auto grids: thin x tick labels.
    tick_step = 2 if len(sizes) <= 24 else max(1, len(sizes) // 12)

    fig = plt.figure(figsize=(12.0, 5.6))
    ax3 = fig.add_subplot(1, 2, 1, projection="3d")
    surf = ax3.plot_surface(
        X, Y, z, cmap="viridis", edgecolor="none", alpha=0.95, antialiased=True
    )
    fig.colorbar(surf, ax=ax3, shrink=0.7, pad=0.12, label="Read throughput (MB/s)")

    ax3.set_xticks(x[::tick_step])
    ax3.set_xticklabels(
        [_fmt_size(n) for n in sizes[::tick_step]], rotation=20, ha="right", fontsize=7
    )
    ax3.set_yticks(y)
    ax3.set_yticklabels([f"s{s}" for s in strides], fontsize=7)
    ax3.set_xlabel("Size (bytes)", labelpad=10)
    ax3.set_ylabel(stride_label, labelpad=8)
    # Avoid clipped "MB/s" on the left of 3D axes; colorbar already carries the unit.
    ax3.set_zlabel("")
    ax3.tick_params(axis="z", pad=8)
    ax3.set_title("Memory mountain (3D) — MB/s")
    ax3.view_init(elev=25, azim=45)
    ax3.set_xlim(0, len(sizes) - 1)
    ax3.set_ylim(0, len(strides) - 1)

    ax2 = fig.add_subplot(1, 2, 2)
    im = ax2.imshow(z, origin="lower", aspect="auto", cmap="viridis")
    fig.colorbar(im, ax=ax2, shrink=0.85, label="MB/s")
    ax2.set_xticks(range(0, len(sizes), tick_step))
    ax2.set_xticklabels(
        [_fmt_size(n) for n in sizes[::tick_step]], rotation=60, ha="right", fontsize=7
    )
    ax2.set_yticks(range(len(strides)))
    ax2.set_yticklabels([f"s{s}" for s in strides], fontsize=8)
    ax2.set_xlabel("Working set size")
    ax2.set_ylabel(stride_label)
    ax2.set_title("Same data (heatmap)")

    # Mark detected cache capacities so hierarchy cliffs are readable at a glance.
    ymax = len(strides) - 0.5
    for label, nbytes in cache_levels:
        idx = nearest_size_index(sizes, nbytes)
        if idx is None:
            continue
        ax2.axvline(idx, color="white", linestyle="--", linewidth=1.0, alpha=0.85)
        ax2.text(
            idx,
            ymax - 0.15,
            f"{label}\n{_fmt_bytes_label(nbytes)}",
            color="white",
            fontsize=7,
            ha="center",
            va="top",
            linespacing=1.1,
            bbox=dict(boxstyle="round,pad=0.15", fc="black", ec="none", alpha=0.35),
        )

    fig.suptitle(host_title(args.host, args.csv), fontsize=10, y=0.98)
    fig.subplots_adjust(left=0.06, right=0.96, bottom=0.12, top=0.88, wspace=0.30)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out, dpi=220, bbox_inches="tight", pad_inches=0.25)
    plt.close(fig)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()

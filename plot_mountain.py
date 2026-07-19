#!/usr/bin/env python3
"""Plot a CSAPP-style memory mountain from mountain.csv (any host)."""

from __future__ import annotations

from pathlib import Path
import argparse
import csv
import json
import warnings

import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parent
DEFAULT_CSV = ROOT / "output" / "mountain.csv"
DEFAULT_HOST = ROOT / "output" / "host_info.json"
DEFAULT_OUT = ROOT / "output" / "memory_mountain.png"


def load(path: Path):
    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
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
    """Compact tick label; prefer clean binary units."""
    if n >= 1 << 20 and n % (1 << 20) == 0:
        return f"{n // (1 << 20)}M"
    if n >= 1 << 10 and n % (1 << 10) == 0:
        return f"{n // (1 << 10)}K"
    if n >= 1 << 20:
        return f"{n / (1 << 20):.0f}M"
    if n >= 1 << 10:
        return f"{n / (1 << 10):.0f}K"
    return str(n)


def _is_pow2(n: int) -> bool:
    return n > 0 and (n & (n - 1)) == 0


def size_tick_indices(sizes: list[int], max_labels: int) -> list[int]:
    """Pick readable size ticks: endpoints + powers of two, thinned to max_labels."""
    n = len(sizes)
    if n == 0:
        return []
    if n <= max_labels:
        return list(range(n))

    prefer = [i for i, s in enumerate(sizes) if _is_pow2(s)]
    if len(prefer) < 2:
        # Fall back to evenly spaced indices.
        step = (n - 1) / (max_labels - 1)
        return sorted({int(round(i * step)) for i in range(max_labels)})

    if len(prefer) > max_labels:
        step = (len(prefer) - 1) / (max_labels - 1)
        prefer = [prefer[int(round(i * step))] for i in range(max_labels)]

    chosen = sorted(set(prefer) | {0, n - 1})
    while len(chosen) > max_labels:
        # Drop the most crowded interior tick.
        best_i = 1
        best_gap = float("inf")
        for i in range(1, len(chosen) - 1):
            gap = chosen[i + 1] - chosen[i - 1]
            if gap < best_gap:
                best_gap = gap
                best_i = i
        del chosen[best_i]
    return chosen


def stride_tick_indices(strides: list[int], max_labels: int = 12) -> list[int]:
    n = len(strides)
    if n <= max_labels:
        return list(range(n))
    step = (n - 1) / (max_labels - 1)
    return sorted({int(round(i * step)) for i in range(max_labels)} | {0, n - 1})


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
            with csv_path.open(newline="") as f:
                rows = list(csv.DictReader(f))
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

    # Large strides are skipped when stride >= n (too few elements). Those cells
    # are NaN — not real DRAM pits. For 3D, carry forward the last valid stride
    # at the same size so the L1 plateau stays flat instead of dropping to z_min.
    z3 = z.astype(float, copy=True)
    for j in range(z3.shape[1]):
        last = np.nan
        for i in range(z3.shape[0]):
            if np.isfinite(z3[i, j]):
                last = z3[i, j]
            elif np.isfinite(last):
                z3[i, j] = last
    if not np.isfinite(z3).all():
        z3 = np.where(np.isfinite(z3), z3, float(np.nanmedian(z)))
    # Heatmap: leave gaps masked so missing samples are obvious, not fake lows.
    z2 = np.ma.masked_invalid(z)

    # Dense auto grids need sparse, power-of-two-biased ticks (esp. 3D projection).
    x_ticks_3d = size_tick_indices(sizes, max_labels=8)
    x_ticks_2d = size_tick_indices(sizes, max_labels=10)
    y_ticks = stride_tick_indices(strides, max_labels=10)

    fig = plt.figure(figsize=(12.4, 5.8))
    ax3 = fig.add_subplot(1, 2, 1, projection="3d")
    surf = ax3.plot_surface(
        X, Y, z3, cmap="viridis", edgecolor="none", alpha=0.95, antialiased=True
    )
    fig.colorbar(surf, ax=ax3, shrink=0.7, pad=0.12, label="Read throughput (MB/s)")

    ax3.set_xticks(x_ticks_3d)
    ax3.set_xticklabels(
        [_fmt_size(sizes[i]) for i in x_ticks_3d],
        rotation=35,
        ha="right",
        va="top",
        fontsize=6.5,
    )
    ax3.set_yticks(y_ticks)
    ax3.set_yticklabels([f"s{strides[i]}" for i in y_ticks], fontsize=6.5)
    ax3.set_xlabel("Size (bytes)", labelpad=14)
    ax3.set_ylabel(stride_label, labelpad=10)
    # Avoid clipped "MB/s" on the left of 3D axes; colorbar already carries the unit.
    ax3.set_zlabel("")
    ax3.tick_params(axis="x", pad=2)
    ax3.tick_params(axis="y", pad=2)
    ax3.tick_params(axis="z", pad=8)
    ax3.set_title("Memory mountain (3D) — MB/s")
    ax3.view_init(elev=28, azim=42)
    ax3.set_xlim(0, len(sizes) - 1)
    ax3.set_ylim(0, len(strides) - 1)

    ax2 = fig.add_subplot(1, 2, 2)
    im = ax2.imshow(z2, origin="lower", aspect="auto", cmap="viridis")
    fig.colorbar(im, ax=ax2, shrink=0.85, label="MB/s")
    ax2.set_xticks(x_ticks_2d)
    ax2.set_xticklabels(
        [_fmt_size(sizes[i]) for i in x_ticks_2d], rotation=45, ha="right", fontsize=7
    )
    ax2.set_yticks(y_ticks)
    ax2.set_yticklabels([f"s{strides[i]}" for i in y_ticks], fontsize=8)
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
    fig.subplots_adjust(left=0.05, right=0.96, bottom=0.16, top=0.88, wspace=0.28)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    # Matplotlib 3.10+ / NumPy 2.x can emit harmless proj3d RuntimeWarnings while
    # projecting 3D tick labels (divide/overflow in the view matrix). Output is fine.
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            category=RuntimeWarning,
            module=r"mpl_toolkits\.mplot3d\.proj3d",
        )
        fig.savefig(args.out, dpi=220, bbox_inches="tight", pad_inches=0.3)
    plt.close(fig)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()

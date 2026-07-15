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
    return sizes, strides, z


def _fmt_size(n: int) -> str:
    if n >= 1 << 20:
        return f"{n // (1 << 20)}m"
    if n >= 1 << 10:
        return f"{n // (1 << 10)}k"
    return str(n)


def host_title(host_path: Path) -> str:
    if host_path.is_file():
        try:
            info = json.loads(host_path.read_text(encoding="utf-8"))
            return info.get("title") or info.get("cpu") or "Memory mountain"
        except (OSError, json.JSONDecodeError):
            pass
    return "Memory mountain"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    ap.add_argument("--host", type=Path, default=DEFAULT_HOST)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    sizes, strides, z = load(args.csv)
    x = np.arange(len(sizes))
    y = np.arange(len(strides))
    X, Y = np.meshgrid(x, y)

    fig = plt.figure(figsize=(11.0, 5.2))
    ax3 = fig.add_subplot(1, 2, 1, projection="3d")
    surf = ax3.plot_surface(
        X, Y, z, cmap="viridis", edgecolor="none", alpha=0.95, antialiased=True
    )
    fig.colorbar(surf, ax=ax3, shrink=0.7, pad=0.1, label="Read throughput (MB/s)")

    ax3.set_xticks(x[::2])
    ax3.set_xticklabels([_fmt_size(n) for n in sizes[::2]], rotation=20, ha="right", fontsize=7)
    ax3.set_yticks(y)
    ax3.set_yticklabels([f"s{s}" for s in strides], fontsize=7)
    ax3.set_xlabel("Size (bytes)", labelpad=8)
    ax3.set_ylabel("Stride (x8 bytes)", labelpad=6)
    ax3.set_zlabel("MB/s", labelpad=4)
    ax3.set_title("Memory mountain (3D)")
    ax3.view_init(elev=25, azim=45)
    ax3.set_xlim(0, len(sizes) - 1)
    ax3.set_ylim(0, len(strides) - 1)

    ax2 = fig.add_subplot(1, 2, 2)
    im = ax2.imshow(z, origin="lower", aspect="auto", cmap="viridis")
    fig.colorbar(im, ax=ax2, shrink=0.85, label="MB/s")
    ax2.set_xticks(range(len(sizes)))
    ax2.set_xticklabels([_fmt_size(n) for n in sizes], rotation=60, ha="right", fontsize=7)
    ax2.set_yticks(range(len(strides)))
    ax2.set_yticklabels([f"s{s}" for s in strides], fontsize=8)
    ax2.set_xlabel("Working set size")
    ax2.set_ylabel("Stride (x8 bytes)")
    ax2.set_title("Same data (heatmap)")

    fig.suptitle(host_title(args.host), fontsize=10, y=1.02)
    fig.subplots_adjust(left=0.04, right=0.96, wspace=0.28)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out, dpi=220, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()

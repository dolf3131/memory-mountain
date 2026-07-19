# Memory Mountain

A portable **CSAPP-style memory mountain** microbenchmark: measure read throughput as a function of **working-set size** and **access stride**, then plot a 3D surface and heatmap.

Works on **Linux**, **macOS**, and **Windows** (MinGW / WSL recommended).

<p align="center">
  <img src="output/memory_mountain.png" alt="Memory mountain example on Apple M4 Pro" width="900"/>
</p>

## Requirements

- C++17 compiler (`g++` or `clang++`)
- Python 3 with `matplotlib` and `numpy`

```bash
pip install matplotlib numpy
```

If `make` fails with a path like `/Users/.../print(1)/bin/python3` (`syntax error near unexpected token '('`), the Python path contains shell metacharacters. Pull the latest Makefile (paths are quoted), or force a normal interpreter:

```bash
make all-run PYTHON=/usr/bin/python3
```

## Quick start

```bash
git clone https://github.com/dolf3131/memory-mountain.git
cd memory-mountain
make all-run          # build → auto cache-aware run → detect host → plot
```

By default `./mountain` uses **`--mode auto`**: it detects the host cache hierarchy (macOS `sysctl`, Linux sysfs; Windows best-effort) and densifies the size×stride grid around L1/L2/(L3) so capacity and spatial-locality cliffs stand out. The heatmap marks those cache sizes.

Artifacts:

| File | Description |
|------|-------------|
| `output/mountain.csv` | Samples: size, stride, MB/s |
| `output/sweep_meta.json` | Detected caches + auto size/stride schedule |
| `output/host_info.json` | Auto-detected CPU / cache summary |
| `output/memory_mountain.png` | 3D surface + heatmap (with cache markers) |

## Options

```bash
# Default: hardware-aware locality sweep
./mountain output/mountain.csv --mode auto

# Original power-of-two CSAPP-style grid
./mountain output/mountain.csv --mode classic
# or: make run-classic

# Faster / lower-memory sweep
./mountain output/mountain.csv --max-bytes 33554432 --seconds 0.05

# Wider sweep
./mountain output/mountain.csv --min-bytes 4096 --max-bytes 268435456 --max-stride 128

python3 detect_host.py
python3 plot_mountain.py
```

## Metal GPU (macOS / Apple Silicon)

Same size × stride sweep on the **GPU** via Metal (shared/unified memory buffers).

```bash
make metal-all        # build → run → plot → output/memory_mountain_metal.png
```

| File | Description |
|------|-------------|
| `mountain.metal` | Compute kernel (`strided_read`) |
| `mountain_metal.mm` | Host timing (`GPUStartTime` / `GPUEndTime`) |
| `output/mountain_metal.csv` | GPU samples |
| `output/memory_mountain_metal.png` | GPU figure |

Notes:

- Element type is `float` (4 B); plot stride axis is labeled `x4 bytes`.
- Requires Xcode CLT (`xcrun clang++`) and a Metal device.
- Apple Silicon GPU caches + unified memory make the surface look different from the CPU mountain; large-stride drops are still the main cliff.

## Fair CPU ↔ GPU comparison

Apple Metal kernels here use **`float`** (FP32). Comparing that to a CPU **`double`** mountain mixes element sizes (4 B vs 8 B), so stride-in-bytes and bytes/load differ.

For an apples-to-apples plot, run both in FP32:

```bash
make compare-cpu-gpu
# CPU float → output/memory_mountain_cpu_f32.png
# GPU Metal → output/memory_mountain_metal.png
```

Or manually:

```bash
./mountain --dtype float output/mountain_cpu_f32.csv
./mountain_metal output/mountain_metal.csv
```

Classic CSAPP-style CPU runs remain available with `--dtype double` (default in `make all-run`).

## What you should see

- **Small size + small stride** → high throughput (data fits in cache; good spatial locality).
- **Large size + large stride** → lower throughput (DRAM-bound; prefetch helps less).
- Modern CPUs with aggressive **hardware prefetch** often keep **unit-stride** bandwidth high even for large arrays; the “valley” is clearer at large strides.
- In **`--mode auto`**, expect denser sampling near L1/L2/(L3) and dashed markers on the heatmap at those capacities.

Cache labels in the plot title come from `sysctl` (macOS), `/sys/devices/system/cpu/.../cache` (Linux), or a best-effort CPU name (Windows). Missing levels are omitted. The binary itself also detects caches for the auto sweep (`output/sweep_meta.json`).

## Repository layout

```
mountain.cpp         # portable C++17 CPU benchmark (--mode auto|classic)
mountain.metal       # Metal GPU kernel (macOS)
mountain_metal.mm    # Metal host
detect_host.py       # CPU / cache detection (plot titles)
plot_mountain.py     # matplotlib 3D + heatmap + cache markers
Makefile             # make all-run / make metal-all
output/              # CSV, sweep_meta.json, host_info*.json, figures
```

## References

1. Randal E. Bryant and David R. O’Hallaron, *Computer Systems: A Programmer’s Perspective*, 3rd ed., Pearson, 2015.  
   — Chapter on the memory hierarchy; the classic “memory mountain” figure and lab inspiration.

2. Randal E. Bryant and David R. O’Hallaron, *CSAPP Student Site* — practice problems and related lab materials:  
   https://csapp.cs.cmu.edu/

3. Ulrich Drepper, “What Every Programmer Should Know About Memory,” 2007.  
   https://people.freebsd.org/~lstewart/articles/cpumemory.pdf  
   — Deeper background on caches, bandwidth, and locality (optional reading).

## Citation

If you use this repository in a report or course project, please cite Bryant & O’Hallaron (CSAPP) as the conceptual source, and optionally this repo for the portable implementation:

```bibtex
@book{bryant2015csapp,
  title     = {Computer Systems: A Programmer's Perspective},
  author    = {Bryant, Randal E. and O'Hallaron, David R.},
  edition   = {3rd},
  year      = {2015},
  publisher = {Pearson},
  address   = {Boston, MA}
}

@misc{memorymountain2026,
  title        = {Memory Mountain: a portable CSAPP-style microbenchmark},
  author       = {Jo, Jeongbin},
  year         = {2026},
  howpublished = {\url{https://github.com/dolf3131/memory-mountain}},
  note         = {GitHub repository}
}
```

## License

MIT — see [LICENSE](LICENSE).

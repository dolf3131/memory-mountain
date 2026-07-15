// Memory mountain: CSAPP-style read throughput vs working-set size and stride.
// Portable C++17: Linux / macOS / Windows (MinGW, MSVC with minor adjustments).
//
// Usage:
//   ./mountain [out.csv] [--min-bytes N] [--max-bytes N] [--max-stride S] [--seconds T]
//
// Defaults match a typical laptop/desktop sweep (8 KiB .. 128 MiB, stride 1..64).

#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <string>
#include <vector>

#if __cplusplus >= 201703L
#include <filesystem>
namespace fs = std::filesystem;
#endif

namespace {

std::size_t g_min_bytes = 1u << 13;   // 8 KiB
std::size_t g_max_bytes = 1u << 27;   // 128 MiB
int g_max_stride = 64;                // elements (x sizeof(double) bytes)
double g_target_seconds = 0.08;       // per (size, stride) sample

volatile double g_sink = 0.0;

double now_seconds()
{
    using clock = std::chrono::steady_clock;
    return std::chrono::duration<double>(clock::now().time_since_epoch()).count();
}

double run_sample(const double* data, std::size_t n, int stride, int reps)
{
    double acc = 0.0;
    const double t0 = now_seconds();
    for (int r = 0; r < reps; ++r) {
        for (std::size_t i = 0; i < n; i += static_cast<std::size_t>(stride)) {
            acc += data[i];
        }
    }
    const double t1 = now_seconds();
    g_sink = acc; // defeat dead-code elimination
    return t1 - t0;
}

void usage(const char* prog)
{
    std::fprintf(stderr,
                 "Usage: %s [out.csv] [--min-bytes N] [--max-bytes N] "
                 "[--max-stride S] [--seconds T]\n",
                 prog);
}

bool parse_args(int argc, char** argv, std::string& out_path)
{
    out_path = "output/mountain.csv";
    for (int i = 1; i < argc; ++i) {
        const char* a = argv[i];
        auto need = [&](std::size_t& dst) -> bool {
            if (i + 1 >= argc) return false;
            dst = static_cast<std::size_t>(std::strtoull(argv[++i], nullptr, 10));
            return dst > 0;
        };
        auto need_int = [&](int& dst) -> bool {
            if (i + 1 >= argc) return false;
            dst = std::atoi(argv[++i]);
            return dst > 0;
        };
        auto need_dbl = [&](double& dst) -> bool {
            if (i + 1 >= argc) return false;
            dst = std::atof(argv[++i]);
            return dst > 0.0;
        };

        if (std::strcmp(a, "--help") == 0 || std::strcmp(a, "-h") == 0) {
            usage(argv[0]);
            std::exit(0);
        }
        if (std::strcmp(a, "--min-bytes") == 0) {
            if (!need(g_min_bytes)) return false;
        } else if (std::strcmp(a, "--max-bytes") == 0) {
            if (!need(g_max_bytes)) return false;
        } else if (std::strcmp(a, "--max-stride") == 0) {
            if (!need_int(g_max_stride)) return false;
        } else if (std::strcmp(a, "--seconds") == 0) {
            if (!need_dbl(g_target_seconds)) return false;
        } else if (a[0] != '-') {
            out_path = a;
        } else {
            return false;
        }
    }
    if (g_min_bytes > g_max_bytes) return false;
    return true;
}

} // namespace

int main(int argc, char** argv)
{
    std::string out_path;
    if (!parse_args(argc, argv, out_path)) {
        usage(argv[0]);
        return 1;
    }

#if __cplusplus >= 201703L
    {
        const fs::path p(out_path);
        if (p.has_parent_path()) {
            std::error_code ec;
            fs::create_directories(p.parent_path(), ec);
        }
    }
#endif

    try {
        std::vector<double> data(g_max_bytes / sizeof(double));
        for (std::size_t i = 0; i < data.size(); ++i) {
            data[i] = static_cast<double>(i & 1023);
        }

        std::ofstream out(out_path);
        if (!out) {
            std::fprintf(stderr, "cannot open %s for write\n", out_path.c_str());
            return 1;
        }
        out << "size_bytes,stride_elems,stride_bytes,throughput_MBps,seconds,reps\n";

        std::fprintf(stderr,
                     "Memory mountain: sizes %zu..%zu bytes, stride 1..%d, ~%.3fs/sample\n",
                     g_min_bytes, g_max_bytes, g_max_stride, g_target_seconds);

        for (std::size_t bytes = g_min_bytes; bytes <= g_max_bytes; bytes *= 2) {
            const std::size_t n = bytes / sizeof(double);
            for (int stride = 1; stride <= g_max_stride; stride *= 2) {
                int reps = 1;
                double dt = run_sample(data.data(), n, stride, reps);
                while (dt < 0.01 && reps < (1 << 24)) {
                    reps *= 2;
                    dt = run_sample(data.data(), n, stride, reps);
                }
                if (dt > 0.0 && dt < g_target_seconds) {
                    const int scale = static_cast<int>(std::ceil(g_target_seconds / dt));
                    reps = std::min(reps * std::max(scale, 1), 1 << 26);
                }
                dt = run_sample(data.data(), n, stride, reps);

                const double loads =
                    static_cast<double>((n + static_cast<std::size_t>(stride) - 1) / stride) * reps;
                const double bytes_read = loads * sizeof(double);
                const double mbps =
                    (dt > 0.0) ? (bytes_read / dt) / (1024.0 * 1024.0) : 0.0;

                out << bytes << ',' << stride << ',' << (stride * static_cast<int>(sizeof(double)))
                    << ',' << mbps << ',' << dt << ',' << reps << '\n';
                std::fprintf(stderr, "  size=%10zu stride=%3d  %10.1f MB/s\n",
                             bytes, stride, mbps);
            }
        }

        std::fprintf(stderr, "wrote %s\n", out_path.c_str());
        return 0;
    } catch (const std::bad_alloc&) {
        std::fprintf(stderr,
                     "out of memory allocating %zu bytes; try a smaller --max-bytes\n",
                     g_max_bytes);
        return 1;
    }
}

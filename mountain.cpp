// Memory mountain: CSAPP-style read throughput vs working-set size and stride.
// Portable C++17: Linux / macOS / Windows (MinGW, MSVC with minor adjustments).
//
// Usage:
//   ./mountain [out.csv] [--mode auto|classic] [--dtype float|double]
//              [--min-bytes N] [--max-bytes N] [--max-stride S] [--seconds T]
//
// --mode auto (default): detect cache hierarchy and densify the size×stride
//   sweep around cache boundaries so locality cliffs are easier to see.
// --mode classic: power-of-two sizes/strides (original CSAPP-style grid).
//
// Use --dtype float for an apples-to-apples comparison with the Metal GPU bench.

#include <algorithm>
#include <cctype>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <set>
#include <string>
#include <type_traits>
#include <vector>

#if defined(__APPLE__)
#include <sys/sysctl.h>
#elif defined(_WIN32)
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#endif

#if __cplusplus >= 201703L
#include <filesystem>
namespace fs = std::filesystem;
#endif

namespace {

std::size_t g_min_bytes = 0; // 0 => choose from host / defaults
std::size_t g_max_bytes = 0;
int g_max_stride = 0;
double g_target_seconds = 0.0; // 0 => mode default
bool g_min_set = false;
bool g_max_set = false;
bool g_stride_set = false;
bool g_seconds_set = false;

enum class DType { Float32, Float64 };
enum class Mode { Auto, Classic };
DType g_dtype = DType::Float64;
Mode g_mode = Mode::Auto;

volatile double g_sink_d = 0.0;
volatile float g_sink_f = 0.0f;

struct HostCaches {
    std::size_t line = 64;
    std::size_t l1d = 0;
    std::size_t l2 = 0;
    std::size_t l3 = 0;
    std::size_t mem = 0;
    std::string source;
};

double now_seconds()
{
    using clock = std::chrono::steady_clock;
    return std::chrono::duration<double>(clock::now().time_since_epoch()).count();
}

std::size_t align_down(std::size_t n, std::size_t a)
{
    if (a == 0) return n;
    return n - (n % a);
}

std::size_t align_up(std::size_t n, std::size_t a)
{
    if (a == 0) return n;
    const std::size_t m = n % a;
    return m == 0 ? n : n + (a - m);
}

std::size_t clamp_sz(std::size_t v, std::size_t lo, std::size_t hi)
{
    return std::max(lo, std::min(v, hi));
}

#if defined(__APPLE__)
bool sysctl_size(const char* name, std::size_t& out)
{
    std::uint64_t v = 0;
    std::size_t len = sizeof(v);
    if (sysctlbyname(name, &v, &len, nullptr, 0) != 0 || v == 0) return false;
    out = static_cast<std::size_t>(v);
    return true;
}
#endif

#if defined(__linux__)
bool read_sysfs_size(const std::string& path, std::size_t& out)
{
    std::ifstream in(path);
    if (!in) return false;
    std::string s;
    in >> s;
    if (s.empty()) return false;
    char unit = 0;
    if ((s.back() == 'K' || s.back() == 'M' || s.back() == 'G') && s.size() > 1) {
        unit = s.back();
        s.pop_back();
    }
    char* end = nullptr;
    const unsigned long long v = std::strtoull(s.c_str(), &end, 10);
    if (end == s.c_str()) return false;
    unsigned long long mult = 1;
    if (unit == 'K') mult = 1024ull;
    else if (unit == 'M') mult = 1024ull * 1024ull;
    else if (unit == 'G') mult = 1024ull * 1024ull * 1024ull;
    out = static_cast<std::size_t>(v * mult);
    return out > 0;
}
#endif

HostCaches detect_caches()
{
    HostCaches c;
#if defined(__APPLE__)
    c.source = "sysctl";
    sysctl_size("hw.cachelinesize", c.line);
    // Prefer performance-core sizes when present (Apple Silicon).
    std::size_t pl1 = 0, pl2 = 0;
    const bool have_p = sysctl_size("hw.perflevel0.l1dcachesize", pl1) &&
                        sysctl_size("hw.perflevel0.l2cachesize", pl2);
    if (have_p) {
        c.l1d = pl1;
        c.l2 = pl2;
        c.source = "sysctl/perflevel0";
    } else {
        sysctl_size("hw.l1dcachesize", c.l1d);
        sysctl_size("hw.l2cachesize", c.l2);
    }
    // Many Apple Silicon parts report no meaningful L3; ignore tiny bogus values.
    std::size_t l3 = 0;
    if (sysctl_size("hw.l3cachesize", l3) && l3 >= (1u << 20)) {
        c.l3 = l3;
    }
    sysctl_size("hw.memsize", c.mem);
#elif defined(__linux__)
    c.source = "sysfs";
    const std::string base = "/sys/devices/system/cpu/cpu0/cache";
    for (int idx = 0; idx < 16; ++idx) {
        const std::string dir = base + "/index" + std::to_string(idx);
        std::ifstream level_in(dir + "/level");
        std::ifstream type_in(dir + "/type");
        if (!level_in || !type_in) continue;
        int level = 0;
        std::string typ;
        level_in >> level;
        type_in >> typ;
        for (char& ch : typ) ch = static_cast<char>(std::tolower(static_cast<unsigned char>(ch)));
        std::size_t nbytes = 0;
        if (!read_sysfs_size(dir + "/size", nbytes)) continue;
        if (typ == "data" || typ == "unified") {
            if (level == 1) c.l1d = nbytes;
            else if (level == 2) c.l2 = nbytes;
            else if (level == 3) c.l3 = nbytes;
        }
        std::size_t line = 0;
        if (read_sysfs_size(dir + "/coherency_line_size", line) && line > 0) {
            c.line = line;
        }
    }
    {
        std::ifstream meminfo("/proc/meminfo");
        std::string key;
        unsigned long long kib = 0;
        std::string unit;
        while (meminfo >> key >> kib >> unit) {
            if (key == "MemTotal:") {
                c.mem = static_cast<std::size_t>(kib) * 1024ull;
                break;
            }
            // discard rest of line if format differs
        }
    }
#elif defined(_WIN32)
    c.source = "windows";
    MEMORYSTATUSEX st{};
    st.dwLength = sizeof(st);
    if (GlobalMemoryStatusEx(&st)) {
        c.mem = static_cast<std::size_t>(st.ullTotalPhys);
    }
    // Best-effort defaults when Win32 cache enumeration is unavailable.
    c.l1d = 32u << 10;
    c.l2 = 256u << 10;
    c.l3 = 8u << 20;
    c.line = 64;
#else
    c.source = "fallback";
#endif

    if (c.line == 0) c.line = 64;
    if (c.l1d == 0) c.l1d = 32u << 10;
    if (c.l2 == 0) c.l2 = 256u << 10;
    // L3 optional.
    return c;
}

std::size_t round_nice(std::size_t n, std::size_t line)
{
    // Keep multiples of cache line (and at least 64 B) so sizes map cleanly to lines.
    const std::size_t a = std::max(line, std::size_t(64));
    if (n < a) return a;
    return align_up(n, a);
}

std::vector<std::size_t> build_size_schedule(const HostCaches& c)
{
    const std::size_t llc = std::max(c.l2, c.l3 == 0 ? c.l2 : c.l3);
    std::size_t min_b;
    std::size_t max_b;
    if (g_mode == Mode::Classic) {
        min_b = g_min_set ? g_min_bytes : (std::size_t(1) << 13);  // 8 KiB
        max_b = g_max_set ? g_max_bytes : (std::size_t(1) << 27);  // 128 MiB
    } else {
        min_b = g_min_set ? g_min_bytes : std::max(std::size_t(4096), c.l1d / 8);
        max_b = g_max_set ? g_max_bytes : std::max(llc * 8, std::size_t(1) << 26);
        if (!g_max_set && c.mem > 0) {
            const std::size_t mem_cap = c.mem / 8; // stay well under RAM
            max_b = std::min(max_b, mem_cap);
        }
        max_b = std::min(max_b, std::size_t(1) << 29); // 512 MiB hard cap
    }
    min_b = clamp_sz(min_b, 4096, max_b);
    max_b = std::max(max_b, min_b);

    std::set<std::size_t> sizes;

    if (g_mode == Mode::Classic) {
        for (std::size_t b = min_b; b <= max_b; b *= 2) sizes.insert(b);
        return std::vector<std::size_t>(sizes.begin(), sizes.end());
    }

    // Dense geometric backbone (√2) so transitions are not missed between powers of two.
    for (double b = static_cast<double>(min_b); b <= static_cast<double>(max_b) * 1.001;
         b *= std::sqrt(2.0)) {
        sizes.insert(round_nice(static_cast<std::size_t>(std::llround(b)), c.line));
    }

    // Extra landmarks around each detected cache level (capacity cliffs).
    const std::size_t levels[] = {c.l1d, c.l2, c.l3};
    const double factors[] = {0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0};
    for (std::size_t level : levels) {
        if (level == 0) continue;
        for (double f : factors) {
            const double v = static_cast<double>(level) * f;
            if (v < static_cast<double>(min_b) || v > static_cast<double>(max_b)) continue;
            sizes.insert(round_nice(static_cast<std::size_t>(std::llround(v)), c.line));
        }
    }

    // Guarantee endpoints.
    sizes.insert(round_nice(min_b, c.line));
    sizes.insert(align_down(max_b, std::max(c.line, std::size_t(64))));
    sizes.erase(0);

    return std::vector<std::size_t>(sizes.begin(), sizes.end());
}

std::vector<int> build_stride_schedule(const HostCaches& c, std::size_t elem_bytes)
{
    int max_stride = g_stride_set ? g_max_stride : 64;
    if (!g_stride_set && g_mode == Mode::Auto) {
        // Cover from unit stride up to several cache lines and beyond, in elements.
        const int line_elems =
            std::max(1, static_cast<int>((c.line + elem_bytes - 1) / elem_bytes));
        max_stride = std::max(64, line_elems * 16);
        // Keep runtime sane on huge lines.
        max_stride = std::min(max_stride, 512);
    }

    std::vector<int> strides;
    for (int s = 1; s <= max_stride; s *= 2) strides.push_back(s);

    if (g_mode == Mode::Auto) {
        // Also sample "one element per cache line" and 2/4 lines — sharp spatial-locality edges.
        const int line_elems =
            std::max(1, static_cast<int>((c.line + elem_bytes - 1) / elem_bytes));
        for (int m : {1, 2, 4, 8}) {
            const int s = line_elems * m;
            if (s > 1 && s <= max_stride) strides.push_back(s);
        }
        std::sort(strides.begin(), strides.end());
        strides.erase(std::unique(strides.begin(), strides.end()), strides.end());
    }
    return strides;
}

// Locality-focused kernel: scalar, dependency chain kept, vectorization discouraged
// so cliffs reflect cache/line behavior rather than compiler SIMD differences.
template <typename T>
double run_sample(const T* data, std::size_t n, int stride, int reps)
{
    T acc0 = T(0);
    T acc1 = T(0);
    const double t0 = now_seconds();
#if defined(__clang__)
#pragma clang loop vectorize(disable) interleave(disable)
#elif defined(__GNUC__)
#pragma GCC unroll 1
#endif
    for (int r = 0; r < reps; ++r) {
        // Two independent chains: enough ILP to expose bandwidth cliffs without
        // full SIMD, while still stressing spatial locality via stride.
        std::size_t i = 0;
        for (; i + static_cast<std::size_t>(stride) < n; i += static_cast<std::size_t>(2 * stride)) {
            acc0 = static_cast<T>(acc0 + data[i]);
            acc1 = static_cast<T>(acc1 + data[i + static_cast<std::size_t>(stride)]);
        }
        for (; i < n; i += static_cast<std::size_t>(stride)) {
            acc0 = static_cast<T>(acc0 + data[i]);
        }
    }
    const double t1 = now_seconds();
    const T acc = static_cast<T>(acc0 + acc1);
    if constexpr (std::is_same_v<T, float>) {
        g_sink_f = acc;
    } else {
        g_sink_d = static_cast<double>(acc);
    }
    return t1 - t0;
}

void usage(const char* prog)
{
    std::fprintf(stderr,
                 "Usage: %s [out.csv] [--mode auto|classic] [--dtype float|double]\n"
                 "              [--min-bytes N] [--max-bytes N] [--max-stride S] [--seconds T]\n"
                 "  --mode auto     detect caches; dense size×stride around hierarchy (default)\n"
                 "  --mode classic  power-of-two grid (original style)\n"
                 "  --dtype float   recommended for comparing with Metal GPU\n"
                 "  --dtype double  classic CSAPP-style (default)\n",
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
        if (std::strcmp(a, "--mode") == 0) {
            if (i + 1 >= argc) return false;
            const char* m = argv[++i];
            if (std::strcmp(m, "auto") == 0) {
                g_mode = Mode::Auto;
            } else if (std::strcmp(m, "classic") == 0) {
                g_mode = Mode::Classic;
            } else {
                return false;
            }
        } else if (std::strcmp(a, "--dtype") == 0) {
            if (i + 1 >= argc) return false;
            const char* d = argv[++i];
            if (std::strcmp(d, "float") == 0 || std::strcmp(d, "f32") == 0) {
                g_dtype = DType::Float32;
            } else if (std::strcmp(d, "double") == 0 || std::strcmp(d, "f64") == 0) {
                g_dtype = DType::Float64;
            } else {
                return false;
            }
        } else if (std::strcmp(a, "--min-bytes") == 0) {
            if (!need(g_min_bytes)) return false;
            g_min_set = true;
        } else if (std::strcmp(a, "--max-bytes") == 0) {
            if (!need(g_max_bytes)) return false;
            g_max_set = true;
        } else if (std::strcmp(a, "--max-stride") == 0) {
            if (!need_int(g_max_stride)) return false;
            g_stride_set = true;
        } else if (std::strcmp(a, "--seconds") == 0) {
            if (!need_dbl(g_target_seconds)) return false;
            g_seconds_set = true;
        } else if (a[0] != '-') {
            out_path = a;
        } else {
            return false;
        }
    }
    if (g_min_set && g_max_set && g_min_bytes > g_max_bytes) return false;
    return true;
}

bool write_sweep_meta(const std::string& csv_path, const HostCaches& c,
                      const std::vector<std::size_t>& sizes, const std::vector<int>& strides,
                      const char* dtype_name)
{
#if __cplusplus >= 201703L
    fs::path csv(csv_path);
    fs::path meta = csv.parent_path() / "sweep_meta.json";
    if (csv.parent_path().empty()) meta = "sweep_meta.json";
#else
    std::string meta = "output/sweep_meta.json";
#endif
    std::ofstream out(
#if __cplusplus >= 201703L
        meta.string()
#else
        meta
#endif
    );
    if (!out) return false;

    const char* mode = g_mode == Mode::Auto ? "auto" : "classic";
    out << "{\n";
    out << "  \"mode\": \"" << mode << "\",\n";
    out << "  \"dtype\": \"" << dtype_name << "\",\n";
    out << "  \"detect_source\": \"" << c.source << "\",\n";
    out << "  \"caches\": {\n";
    out << "    \"line\": " << c.line << ",\n";
    out << "    \"L1d\": " << c.l1d << ",\n";
    out << "    \"L2\": " << c.l2 << ",\n";
    out << "    \"L3\": " << c.l3 << ",\n";
    out << "    \"mem\": " << c.mem << "\n";
    out << "  },\n";
    out << "  \"sizes\": [";
    for (std::size_t i = 0; i < sizes.size(); ++i) {
        if (i) out << ", ";
        out << sizes[i];
    }
    out << "],\n  \"strides\": [";
    for (std::size_t i = 0; i < strides.size(); ++i) {
        if (i) out << ", ";
        out << strides[i];
    }
    out << "]\n}\n";
#if __cplusplus >= 201703L
    std::fprintf(stderr, "wrote %s\n", meta.string().c_str());
#else
    std::fprintf(stderr, "wrote %s\n", meta.c_str());
#endif
    return true;
}

template <typename T>
int run_mountain(const std::string& out_path, const HostCaches& caches)
{
    if (!g_seconds_set) {
        g_target_seconds = (g_mode == Mode::Auto) ? 0.05 : 0.08;
    }

    const auto sizes = build_size_schedule(caches);
    const auto strides = build_stride_schedule(caches, sizeof(T));
    if (sizes.empty() || strides.empty()) {
        std::fprintf(stderr, "empty size/stride schedule\n");
        return 1;
    }

    const std::size_t alloc_bytes = sizes.back();
    std::vector<T> data(alloc_bytes / sizeof(T));
    for (std::size_t i = 0; i < data.size(); ++i) {
        data[i] = static_cast<T>(i & 1023);
    }

    std::ofstream out(out_path);
    if (!out) {
        std::fprintf(stderr, "cannot open %s for write\n", out_path.c_str());
        return 1;
    }
    out << "size_bytes,stride_elems,stride_bytes,throughput_MBps,seconds,reps,dtype\n";

    const char* dtype_name = std::is_same_v<T, float> ? "float" : "double";
    const char* mode_name = g_mode == Mode::Auto ? "auto" : "classic";
    std::fprintf(stderr,
                 "Memory mountain (%s, mode=%s): %zu sizes [%zu..%zu], %zu strides, "
                 "~%.3fs/sample\n",
                 dtype_name, mode_name, sizes.size(), sizes.front(), sizes.back(), strides.size(),
                 g_target_seconds);
    std::fprintf(stderr, "  caches(%s): line=%zu L1d=%zu L2=%zu L3=%zu\n", caches.source.c_str(),
                 caches.line, caches.l1d, caches.l2, caches.l3);

    write_sweep_meta(out_path, caches, sizes, strides, dtype_name);

    for (std::size_t bytes : sizes) {
        const std::size_t n = bytes / sizeof(T);
        if (n == 0) continue;
        for (int stride : strides) {
            if (static_cast<std::size_t>(stride) >= n) continue;

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
            const double bytes_read = loads * sizeof(T);
            const double mbps = (dt > 0.0) ? (bytes_read / dt) / (1024.0 * 1024.0) : 0.0;

            out << bytes << ',' << stride << ',' << (stride * static_cast<int>(sizeof(T))) << ','
                << mbps << ',' << dt << ',' << reps << ',' << dtype_name << '\n';
            std::fprintf(stderr, "  size=%10zu stride=%3d  %10.1f MB/s\n", bytes, stride, mbps);
        }
    }

    std::fprintf(stderr, "wrote %s\n", out_path.c_str());
    return 0;
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

    const HostCaches caches = detect_caches();

    try {
        if (g_dtype == DType::Float32) {
            return run_mountain<float>(out_path, caches);
        }
        return run_mountain<double>(out_path, caches);
    } catch (const std::bad_alloc&) {
        std::fprintf(stderr,
                     "out of memory allocating working set; try a smaller --max-bytes\n");
        return 1;
    }
}

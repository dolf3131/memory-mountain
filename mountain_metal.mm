// Metal GPU memory mountain (macOS / Apple Silicon).
// Same size × stride sweep as the CPU benchmark; measures GPU read throughput.
//
// Build (macOS only):
//   make metal
//   ./mountain_metal output/mountain_metal.csv

#import <Foundation/Foundation.h>
#import <Metal/Metal.h>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <string>
#include <vector>

namespace {

std::size_t g_min_bytes = 1u << 13;    // 8 KiB
std::size_t g_max_bytes = 1u << 27;    // 128 MiB
int g_max_stride = 64;                 // elements (x4 bytes for float)
double g_target_seconds = 0.05;
uint32_t g_threads = 65536;            // dispatch width (capped by device)

void usage(const char* prog)
{
    std::fprintf(stderr,
                 "Usage: %s [out.csv] [--min-bytes N] [--max-bytes N] "
                 "[--max-stride S] [--seconds T] [--threads N]\n"
                 "  Metal GPU memory mountain (macOS). Stride is in float elements.\n",
                 prog);
}

bool parse_args(int argc, char** argv, std::string& out_path)
{
    out_path = "output/mountain_metal.csv";
    for (int i = 1; i < argc; ++i) {
        const char* a = argv[i];
        auto need_sz = [&](std::size_t& dst) -> bool {
            if (i + 1 >= argc) return false;
            dst = static_cast<std::size_t>(std::strtoull(argv[++i], nullptr, 10));
            return dst > 0;
        };
        auto need_int = [&](int& dst) -> bool {
            if (i + 1 >= argc) return false;
            dst = std::atoi(argv[++i]);
            return dst > 0;
        };
        auto need_u32 = [&](uint32_t& dst) -> bool {
            if (i + 1 >= argc) return false;
            dst = static_cast<uint32_t>(std::strtoul(argv[++i], nullptr, 10));
            return dst > 0;
        };
        auto need_dbl = [&](double& dst) -> bool {
            if (i + 1 >= argc) return false;
            dst = std::atof(argv[++i]);
            return dst > 0.0;
        };

        if (std::strcmp(a, "-h") == 0 || std::strcmp(a, "--help") == 0) {
            usage(argv[0]);
            std::exit(0);
        } else if (std::strcmp(a, "--min-bytes") == 0) {
            if (!need_sz(g_min_bytes)) return false;
        } else if (std::strcmp(a, "--max-bytes") == 0) {
            if (!need_sz(g_max_bytes)) return false;
        } else if (std::strcmp(a, "--max-stride") == 0) {
            if (!need_int(g_max_stride)) return false;
        } else if (std::strcmp(a, "--seconds") == 0) {
            if (!need_dbl(g_target_seconds)) return false;
        } else if (std::strcmp(a, "--threads") == 0) {
            if (!need_u32(g_threads)) return false;
        } else if (a[0] != '-') {
            out_path = a;
        } else {
            return false;
        }
    }
    return g_min_bytes <= g_max_bytes;
}

NSString* load_metal_source()
{
    // Prefer mountain.metal next to cwd, then next to the executable.
    NSFileManager* fm = [NSFileManager defaultManager];
    NSArray<NSString*>* candidates = @[
        @"mountain.metal",
        @"./mountain.metal",
    ];
    NSString* exec = [[NSProcessInfo processInfo] arguments].firstObject;
    if (exec) {
        NSString* dir = [exec stringByDeletingLastPathComponent];
        candidates = [candidates arrayByAddingObject:[dir stringByAppendingPathComponent:@"mountain.metal"]];
    }
    for (NSString* path in candidates) {
        if ([fm fileExistsAtPath:path]) {
            NSError* err = nil;
            NSString* src = [NSString stringWithContentsOfFile:path
                                                     encoding:NSUTF8StringEncoding
                                                        error:&err];
            if (src) return src;
        }
    }
    return nil;
}

} // namespace

int main(int argc, char** argv)
{
    @autoreleasepool {
        std::string out_path;
        if (!parse_args(argc, argv, out_path)) {
            usage(argv[0]);
            return 1;
        }

        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        if (!device) {
            std::fprintf(stderr, "Metal: no GPU device available\n");
            return 1;
        }
        std::fprintf(stderr, "Metal device: %s\n", [[device name] UTF8String]);

        NSString* src = load_metal_source();
        if (!src) {
            std::fprintf(stderr, "cannot find mountain.metal (run from the repo directory)\n");
            return 1;
        }

        NSError* err = nil;
        id<MTLLibrary> lib = [device newLibraryWithSource:src options:nil error:&err];
        if (!lib) {
            std::fprintf(stderr, "Metal compile failed: %s\n",
                         err ? [[err localizedDescription] UTF8String] : "?");
            return 1;
        }
        id<MTLFunction> fn = [lib newFunctionWithName:@"strided_read"];
        if (!fn) {
            std::fprintf(stderr, "kernel strided_read not found\n");
            return 1;
        }
        id<MTLComputePipelineState> pso =
            [device newComputePipelineStateWithFunction:fn error:&err];
        if (!pso) {
            std::fprintf(stderr, "PSO failed: %s\n",
                         err ? [[err localizedDescription] UTF8String] : "?");
            return 1;
        }
        id<MTLCommandQueue> queue = [device newCommandQueue];

        // Shared buffers on Apple Silicon (unified memory).
        const NSUInteger max_floats = g_max_bytes / sizeof(float);
        id<MTLBuffer> dataBuf =
            [device newBufferWithLength:max_floats * sizeof(float)
                               options:MTLResourceStorageModeShared];
        id<MTLBuffer> sinkBuf =
            [device newBufferWithLength:sizeof(uint32_t)
                               options:MTLResourceStorageModeShared];
        if (!dataBuf || !sinkBuf) {
            std::fprintf(stderr, "buffer allocation failed (%zu bytes)\n", g_max_bytes);
            return 1;
        }
        float* host = static_cast<float*>([dataBuf contents]);
        for (NSUInteger i = 0; i < max_floats; ++i) {
            host[i] = static_cast<float>(i & 1023);
        }

        // Create output directory if needed (best-effort).
        {
            NSString* nsout = [NSString stringWithUTF8String:out_path.c_str()];
            NSString* dir = [nsout stringByDeletingLastPathComponent];
            if (dir.length > 0) {
                [[NSFileManager defaultManager] createDirectoryAtPath:dir
                                          withIntermediateDirectories:YES
                                                           attributes:nil
                                                                error:nil];
            }
        }

        std::ofstream out(out_path);
        if (!out) {
            std::fprintf(stderr, "cannot write %s\n", out_path.c_str());
            return 1;
        }
        out << "size_bytes,stride_elems,stride_bytes,throughput_MBps,seconds,reps,device\n";

        const uint32_t tg = static_cast<uint32_t>(pso.threadExecutionWidth);
        uint32_t nthreads = g_threads;
        nthreads = std::max(nthreads, tg);
        nthreads = (nthreads / tg) * tg; // multiple of thread execution width

        std::fprintf(stderr,
                     "Metal mountain: sizes %zu..%zu, stride 1..%d (float), threads=%u\n",
                     g_min_bytes, g_max_bytes, g_max_stride, nthreads);

        auto encode_and_time = [&](uint32_t n_elems, uint32_t stride, uint32_t reps) -> double {
            const uint32_t n_loads = (n_elems + stride - 1u) / stride;
            // Match launch size to useful work (avoid empty threads dominating tiny sets).
            uint32_t nthr = std::min(g_threads, std::max(n_loads, tg));
            nthr = ((nthr + tg - 1u) / tg) * tg;

            id<MTLCommandBuffer> cb = [queue commandBuffer];
            id<MTLComputeCommandEncoder> enc = [cb computeCommandEncoder];
            [enc setComputePipelineState:pso];
            [enc setBuffer:dataBuf offset:0 atIndex:0];
            [enc setBuffer:sinkBuf offset:0 atIndex:1];
            [enc setBytes:&n_elems length:sizeof(uint32_t) atIndex:2];
            [enc setBytes:&stride length:sizeof(uint32_t) atIndex:3];
            [enc setBytes:&reps length:sizeof(uint32_t) atIndex:4];
            MTLSize grid = MTLSizeMake(nthr, 1, 1);
            MTLSize group = MTLSizeMake(tg, 1, 1);
            [enc dispatchThreads:grid threadsPerThreadgroup:group];
            [enc endEncoding];
            [cb commit];
            [cb waitUntilCompleted];

            if (@available(macOS 10.15, *)) {
                const CFTimeInterval t0 = cb.GPUStartTime;
                const CFTimeInterval t1 = cb.GPUEndTime;
                if (t1 > t0) return t1 - t0;
            }
            return 0.0;
        };

        const char* devname = [[device name] UTF8String];

        for (std::size_t bytes = g_min_bytes; bytes <= g_max_bytes; bytes *= 2) {
            const uint32_t n_elems = static_cast<uint32_t>(bytes / sizeof(float));
            for (int stride = 1; stride <= g_max_stride; stride *= 2) {
                uint32_t reps = 1;
                double dt = encode_and_time(n_elems, static_cast<uint32_t>(stride), reps);
                // Grow reps until we have enough GPU time for a stable sample.
                while (dt < 0.005 && reps < (1u << 20)) {
                    reps *= 2;
                    dt = encode_and_time(n_elems, static_cast<uint32_t>(stride), reps);
                }
                if (dt > 0.0 && dt < g_target_seconds) {
                    const uint32_t scale =
                        static_cast<uint32_t>(std::ceil(g_target_seconds / dt));
                    reps = std::min(reps * std::max(scale, 1u), 1u << 22);
                    dt = encode_and_time(n_elems, static_cast<uint32_t>(stride), reps);
                }

                const double n_loads =
                    static_cast<double>((n_elems + static_cast<uint32_t>(stride) - 1u) /
                                        static_cast<uint32_t>(stride)) *
                    static_cast<double>(reps);
                const double bytes_read = n_loads * sizeof(float);
                const double mbps =
                    (dt > 0.0) ? (bytes_read / dt) / (1024.0 * 1024.0) : 0.0;

                out << bytes << ',' << stride << ',' << (stride * 4) << ',' << mbps << ','
                    << dt << ',' << reps << ',' << '"' << devname << '"' << '\n';
                std::fprintf(stderr, "  size=%10zu stride=%3d  %10.1f MB/s  (%.4fs x%u)\n",
                             bytes, stride, mbps, dt, reps);
            }
        }

        // Also drop a tiny GPU host note for the plotter.
        {
            std::string hip = out_path;
            const auto slash = hip.find_last_of('/');
            std::string dir = (slash == std::string::npos) ? "output" : hip.substr(0, slash);
            std::ofstream hi(dir + "/host_info_metal.json");
            if (hi) {
                hi << "{\n"
                   << "  \"os\": \"macOS\",\n"
                   << "  \"cpu\": \"\",\n"
                   << "  \"gpu\": \"" << devname << "\",\n"
                   << "  \"arch\": \"Metal\",\n"
                   << "  \"title\": \"Metal GPU — " << devname << "\"\n"
                   << "}\n";
            }
        }

        std::fprintf(stderr, "wrote %s\n", out_path.c_str());
        return 0;
    }
}

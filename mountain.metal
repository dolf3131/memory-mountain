#include <metal_stdlib>
using namespace metal;

// Strided read over a float buffer. Thread `gid` owns loads
//   data[gid*stride], data[(gid+grid)*stride], ...
// Host sets n_elems = buffer length in floats; n_loads = ceil(n_elems / stride).
kernel void strided_read(
    device const float* data [[buffer(0)]],
    device atomic_uint* sink [[buffer(1)]],
    constant uint& n_elems [[buffer(2)]],
    constant uint& stride [[buffer(3)]],
    constant uint& reps [[buffer(4)]],
    uint gid [[thread_position_in_grid]],
    uint grid [[threads_per_grid]])
{
    const uint n_loads = (n_elems + stride - 1u) / stride;
    float acc = 0.0f;
    for (uint r = 0u; r < reps; ++r) {
        for (uint i = gid; i < n_loads; i += grid) {
            const uint idx = i * stride;
            // idx < n_elems by construction when i < n_loads and stride >= 1
            acc += data[idx];
        }
    }
    // Defeat DCE without a heavyweight reduction.
    if (acc > 1.0e30f) {
        atomic_fetch_add_explicit(sink, 1u, memory_order_relaxed);
    }
}

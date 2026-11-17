# Memory (12 GB reference card)

**These are estimates, not measurements. There was no GPU in the development environment.** Run
`python -m training.shared.measure_memory` on the RTX 3060 to replace them with real numbers (it writes `docs/memory_measured.json`).

The estimator (`python -m training.shared.memory_estimate`) counts the bytes PyTorch really saves for the backward pass (measured
with saved-tensor hooks at the real input size), plus weights, gradients and Adam state, adds a margin for the CUDA context, and
applies about 0.55x for FP16 autocast.

| Model | Setting | Parameters | Training peak (estimate) | Inference (estimate) |
|---|---|---|---|---|
| General | 512 x 512, batch 4, FP16, accumulate 4 | 4.9 M | about 2.9 GB | about 0.4 GB |
| Hair | 256 x 256, batch 8, FP16, accumulate 2 | 1.1 M | about 1.4 GB | about 0.3 GB |
| Inpainting | 256 x 256, batch 8, FP16 | 7.2 M | about 2.3 GB | about 0.35 GB |

**Both models in one Make Layers + hair job:** the general model runs first and the hair model later; both ONNX sessions stay resident:
about **0.7 GB** together. Each session's CUDA arena is capped (`device.gpu_mem_limit_mb`, default 4096) so two models plus the
upscaler (which uses its own Vulkan memory, tiled and retried smaller after an out-of-memory failure) stay inside 12 GB.

Training one model at a time is enforced with a lock (`models/.training.lock`). With these estimates a batch of 8 or more fits on the
general model; the shipped config (batch 4, accumulate 4, effective 16) is conservative.

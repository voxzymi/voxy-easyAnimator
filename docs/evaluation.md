# Evaluation: what was measured, and what it means

**Read this before quoting any number.** No real anime artwork was available, no GPU was available, and the models below were
trained for about 25 minutes each on a 4-core CPU at 128 px, on synthetic data only. The numbers prove that the
train, export, load, evaluate loop works and that the model learns. They are **not** a quality claim about real art.

## The evaluation tool

`python -m training.shared.evaluate` reports separately for each model and each kind of data (see [training.md](training.md)):

- General model: per-part IoU, **Hair IoU** (the single Hair class), group-assignment accuracy (pixel and part level), key-point
  **PCK@0.05 and PCK@0.10** (a joint is right when it is within that fraction of the character's height), hidden-extent IoU,
  completion error, and a contact sheet.
- Hair model: flow angular error, root-point F1 (4 px), subdivision matched-IoU against the true strand sections, with a trivial
  vertical-flow baseline next to it, and a contact sheet.
- "Held-out real art" is the `--real` / `--hair-real` folders (corrected real images that were **not** used for training).
  **None exists yet, so no real-art number was measured.** The tool says so in its report instead of printing zeros.
- The tool runs the exported ONNX files through the service's own runners, so it evaluates what would ship.

## The run that was done (CPU, synthetic only)

| | |
|---|---|
| Data | 600 sources x 3 variants at 160x120, split **by source** 480 / 60 / 60 (train / val / test). Evaluated on the test split: 180 pictures. |
| General model | U-Net, 2.8 M parameters, input 128 px, batch 8, lr 1.5e-3, 14 epochs, 2520 steps, 26 min, CPU, no FP16 (FP16 is CUDA-only). ONNX exported; parity with PyTorch checked on 10 real inputs: max difference 2e-4, identical labels on all of them. (The export step itself only checks an all-zeros input, which is a weak test: a stronger check on real inputs should be added to `export_model`.) |
| Hair model | U-Net, input 128 px, 14 epochs, 2520 steps, 22 min, CPU, hair data only. |

### General model, synthetic test split (evaluated at the picture's own resolution)

| Metric | Value |
|---|---|
| Mean per-part IoU (21 parts) | **0.61** |
| **Hair IoU** | **0.88** |
| Head / torso / legs / arms (IoU) | 0.80 / 0.90 / 0.84-0.87 / 0.70-0.78 |
| Thin or tiny parts (eyelashes, eyebrows, eyes, nose) | 0.22-0.41 |
| Group assignment accuracy | 0.92 per pixel, 0.93 per part |
| Key-point PCK@0.05 / PCK@0.10 (of character height) | 1.00 / 1.00 |
| Hidden-part extent IoU | 0.80 |
| Completion error (mean absolute, 0-1 scale) | 0.087 |

Contact sheet: `docs/images/eval/contact_general_synthetic.png` (input, truth, prediction).

**Why the training log said 0.80 and this says 0.61.** Training validated at the model's own 128 px resolution (0.798). Scoring the *same
predictions* at the picture's real resolution gives 0.61, because 1-3 px parts cannot survive the resize: even a **perfect** answer pushed
through the 128 px round trip scores only 0.485 against the full-resolution truth. The full-resolution number is the honest one.
The shipped config uses 512 px inputs on 512 px pictures (no downscale), so this particular loss should mostly go away.

PCK of 1.00 looks too good and is: the synthetic characters are very regular, so joints sit in predictable places relative to the
silhouette. Expect much lower on real art.

### Hair model, synthetic test split

| Metric | Value |
|---|---|
| Flow angular error, mean / median | 28.6 / 28.1 degrees |
| Root-point F1 (4 px tolerance) | 0.62 |
| Subdivision quality (matched IoU vs true strand sections) | **0.38** |
| Trivial baseline (every strand points straight down) | **0.40** |

**The hair model does not beat the trivial baseline.** `docs/images/eval/sample_pipeline.png` shows it on the bundled sample: the
parts reassemble almost perfectly (middle), but the hair sections (right) are wedges fanning from the top of the head. The geometry
is right (the sections cover the Hair part exactly, so Combine is exact); the *flow field the model predicts* is not good enough.
Needs a real run: larger input, GPU, more epochs, and real labeled hair.

### Speed on this CPU

Make Layers on the 768x512 sample with the 128 px ONNX model: load 15 ms, model 218 ms, post-processing 152 ms. Hair sections: 60 ms.

## What is not measured

- Anything on real art. Anything on a GPU. Peak VRAM (only [estimated](memory.md)). The inpainting and matting models (no network
  ships; the flat-color fill and the flood-fill key are tested as algorithms, with unit tests, not as models).
- The raw report is `docs/images/eval/report-cpu-smoke-0.0.2.json`. The trained weights are **not** committed.

# Training and data: how to run it

All commands run from the repository root. `pip install -r service/requirements-dev.txt scipy torch torchvision` first.

## 1. Make synthetic data (Phase 2)

```
python -m training.shared.generate_dataset --out data/synthetic --sources 400 --variants 4 --seed 1 --workers 3
```

Each sample (under `data/synthetic/<train|val|test>/<id>/`) has `image.png`, `labels.png` (hair is one class), one `masks/<part>.png` per
part, `amodal.npz` (full extents and hidden regions), `completion.png` (covered parts un-covered; target for completion and
inpainting), `hair.npz` (hair mask, strand flow field, root points, strand sections: for the hair model only) and `sample.json`
(parts, masks, key points, z-order, group, which model each label is for, license, sources).

- Parts are drawn by the script, so every mask is exact; stacking order is randomised (arm and leg order) and recorded.
- Hair strands are traced along a random flow field that grows from the root points and stored as sections of **one** Hair mask.
- Augmentation: line thickness jitter, mirroring (left/right swapped), colour shifts, flat/gradient/noise backgrounds, JPEG and scan artefacts.
- **Splits are by source** (one source = one character identity or reference sheet), so variants of one source never land in two splits.
- Reference sheets in `data/raw/` are used for hair pieces (random rotation, scale, flips, colour shift) **only** if `PROVENANCE.csv` has a
  row with a known license or owner permission. Audit with `python -m training.shared.provenance audit`.

## 2. Label real art and bootstrap (Phase 3)

```
python -m training.shared.label_tool.server --images data/raw --out data/labeled      # http://127.0.0.1:8765
python -m training.shared.bootstrap predict --images data/raw --general models/general/0.1.0 --hair models/hair/0.1.0
python -m training.shared.bootstrap round --model general --prelabel-version 0.1.0 --trained-version 0.2.0 --notes "..."
python -m training.shared.bootstrap status
```

The browser tool corrects predicted masks, key points and groups (a group follows the class). It keeps **two label sets in separate
folders**: `data/labeled/general/<image>/` (labels with hair as one class, key points) and `data/labeled/hair/<image>/` (strand sections
inside the hair mask and root points). The loop: train v1 on synthetic data, pre-label the real images with it, correct, add the corrected
images to the matching training set (`data.labeled` in the config), retrain, and record the round (`data/labeled/rounds.json`).

## 3. Train (Phase 4). One job at a time, never in parallel

```
python -m training.general.train --config configs/general.yaml      # segmentation, key points, hidden extents, completion
python -m training.hair.train    --config configs/hair.yaml         # flow field + roots (hair data only)
python -m training.inpaint.train --config configs/inpaint.yaml [--freeze-encoder] [--no-perceptual]
```

FP16 autocast + gradient accumulation (set in the config) keep each job inside 12 GB. Override any setting: `--set train.epochs=5`.
A second job started while one runs is refused (lock file). Checkpoints are per model and version
(`out/<model>/<version>/`) and a run refuses to resume another model's checkpoint.

## 4. Export and evaluate

```
python -m training.general.export_onnx --checkpoint out/general/0.1.0/best.pt --version 0.1.0     # -> models/general/0.1.0/
python -m training.hair.export_onnx    --checkpoint out/hair/0.1.0/best.pt --version 0.1.0
python -m training.shared.evaluate --general models/general/0.1.0 --hair models/hair/0.1.0 \
    --synthetic data/synthetic/test --real data/labeled/general --hair-real data/labeled/hair --images data/raw --out out/eval
```

Export checks ONNX against PyTorch (fails if they differ) and writes `model.json`. The report is **separate for each model and for
each kind of data**: general: per-part IoU, Hair IoU, group accuracy, key-point **PCK@0.05 and PCK@0.10** of the character's height,
hidden-extent IoU, completion error; hair: flow angular error, root F1 (4 px), subdivision matched-IoU vs a vertical-flow baseline;
plus contact sheets. "Held-out real art" means `--real` / `--hair-real` folders that were **not** added to the training sets.
See [evaluation.md](evaluation.md) for what has and has not been measured.

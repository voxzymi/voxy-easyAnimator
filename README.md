# Voxy EasyAnimator

An After Effects plug-in that cuts a flat anime character layer into organized, animatable layers, and a local machine-learning
service that does the cutting. Everything runs on the user's computer.

> **Status: development version 0.1.0.** The service, training pipeline, host logic and panel are built and tested. The After Effects
> SDK adapter, the Windows installer, GPU runs and real-art training could **not** be done in the development environment. Read
> [What is and is not verified](#what-is-and-is-not-verified) first, and [docs/decisions-needed.md](docs/decisions-needed.md).

Docs: [architecture](docs/architecture.md) · [manifest](docs/manifest.md) · [local interfaces](docs/interface.md) · [models](docs/models.md) ·
[training](docs/training.md) · [evaluation](docs/evaluation.md) · [memory](docs/memory.md) · [panel technology](docs/panel-technology.md) ·
[AE test checklist](docs/ae_test_checklist.md) · [install test checklist](docs/install_test_checklist.md) · user guide [INSTALL.md](docs/INSTALL.md)

## Layout

```
ae_host/            C++ After Effects host and the HTML panel
  core/             portable C++: Add to Project, Restore, autosave, bridge, service supervisor (tested against a fake AE)
  aegp/             the After Effects SDK adapter + entry point (NOT compiled in development)
  panel/            CEP panel: js/core (logic), js/api, js/ui, js/adapters, tests/ (Node) and tests/ui (browser)
  tests/            C++ tests, fake After Effects, fixture generator
service/            local ML service (Python): voxy_service/, tests/
training/general/   general dissection model (train, export)      training/hair/   hair subdivision model
training/inpaint/   inpainting fine-tune                          training/shared/ dataset generator, labeling tool, evaluation
configs/            service and training settings                  data/raw/        reference images + PROVENANCE.csv
data/synthetic/     generated samples (git-ignored)                models/<name>/<version>/  versioned ONNX files
installer/          Inno Setup script, build scripts, third-party list      docs/   all documentation
```

## Set up

```
python -m pip install -r service/requirements-dev.txt scipy torch torchvision     # Python 3.10+; torch only for training
(cd ae_host/panel && npm install --no-save playwright)                             # only for the browser UI test
```

## Run the tests

```
(cd service && PYTHONPATH=$PWD python -m pytest tests -q)                         # 99 tests: manifest, groups, failure paths, hair, upscale, ...
PYTHONPATH=service/tests python -m pytest training/tests installer -q              # generator, labeling, training, export, evaluation, help
cmake -S ae_host -B ae_host/build && cmake --build ae_host/build -j && (cd ae_host/build && ctest --output-on-failure)   # 53 host tests
(cd ae_host/panel && node --test "tests/*.test.js")                                # 79 panel logic tests
```

The browser UI test (`ae_host/panel/tests/ui/ui.smoke.js`, 42 checks) starts the real service and a stand-in for the After Effects
bridge; see `.github/workflows/ci.yml` for how CI runs everything.

## Start the service

```
cd service && python -m voxy_service --port 0 --data-dir /tmp/voxy        # prints the port; writes service.json {port, token}
python -m voxy_service --selftest                                          # health check + cut the sample: exit 0 / 1
python -m voxy_service --preflight --plain                                 # the setup checks in plain words
```

The default config uses **stub models** (a fixed template laid over the silhouette) so the pipeline works before real models exist.
Release builds set `allow_stub_models: false` (`configs/service.release.yaml`) and need `models/general/<version>/`.

Package without a separate Python: `cd service && pyinstaller voxy_service.spec --noconfirm` (verified on Linux; see `installer/`).

## Build the AEGP and the panel

- **Panel**: no build step. Copy `ae_host/panel/` to the CEP extensions folder (the installer does this). Rebuild the help page with
  `python installer/build_help.py`.
- **AEGP**: download the After Effects SDK from Adobe (not redistributable), then
  `cmake -S ae_host -B build -DVOXY_BUILD_AEGP=ON -DAE_SDK_DIR=<sdk>`. `ae_host/aegp/SDK_NOTES.md` lists every SDK call and how sure I am
  of it. **It has never been compiled.**
- **Installer** (Windows): `powershell -File installer/build_windows.ps1 -Version 0.1.0 -AllowUnconfirmed -UnsignedPanel`.

## Full-screen approach

A docked CEP panel cannot overlay the screen, so full screen is a second floating panel sized to the monitor: the button opens it and
closes the docked one; the user's work follows through a saved session. Limits and reasoning: [docs/panel-technology.md](docs/panel-technology.md).

## How the two models fit together

| | General dissection model | Hair subdivision model |
|---|---|---|
| Sees | the whole character | only the Hair part the general model produced |
| Predicts | part segmentation (hair = one class), 15 key points, hidden-part extents, completion | strand flow field, root points |
| Trained on | full-character labels | hair data: synthetic strands, reference hair, labeled sections |
| Runs | first, for every Make Layers | only when the user presses *Cut hair into subdivisions* |
| If it fails | Make Layers fails, **no parts** | the single Hair part stays; the failure is recorded |

Own code, config, weights and version each; trained at different times (a lock enforces one training job); neither imports the other.

## Groups, eyes, Add to Project

Every part has a `group` (Head, Face, Hair, Torso, Arms, Legs, Hidden) from a fixed class table. Parts keep manifest order under their
group. **Eye buttons change only the panel** (what it previews and what Add to Project uses); they never send a command to After Effects,
and a half-open group eye means some parts are hidden. **Add to Project** adds every visible part (one precomp, or folder, per group that
has one; masked layers with the pivot as anchor; rig parenting) as **one undo step**; **Restore** removes exactly the last Add's layers.
Details: [docs/architecture.md](docs/architecture.md).

## Upscaling, Apply in Project

*Upscale 2x* runs `upscayl-ncnn` on the selected layer's exported frame only (tiled, with out-of-memory retries) into a temporary file and
shows it beside the original. Nothing changes until *Apply in Project* (one undo step: replaces that layer's source, keeps name, masks,
position, keyframes, and looks identical). *Discard* deletes the temporary file. Apply keeps a durable copy next to the project because
After Effects links to files.

## Autosave and crash recovery

A timer in the panel (default 5 minutes) asks the plug-in for a backup **copy** next to the project (`Voxy Autosave/`), written under a
temporary name, checked, then renamed; it never writes the project file and switches itself off if After Effects' save behaves unexpectedly.
After a crash the panel offers the newest backup that is newer than the last manual save. Details in [docs/architecture.md](docs/architecture.md).

## Training each model

See [docs/training.md](docs/training.md): `generate_dataset`, the labeling tool and bootstrap loop, then
`python -m training.general.train` and `python -m training.hair.train` (one at a time), `export_onnx`, `evaluate`.

## What is and is not verified

| Verified here (automated) | Not verified (needs your machine) |
|---|---|
| Manifest schema, group field, validator; friendly errors; the two failure rules; health check, cancel, timeouts (service: 99 tests) | The AEGP plug-in: **never compiled**; real layers, masks, undo, save semantics (see the AE checklist) |
| Hair geometry (partition, flow direction, smoothing, Combine = union); background fill + edge blend; upscaler wrapper with a stand-in tool | A real `upscayl-ncnn`, Vulkan, the RTX 3060, CUDA, measured VRAM |
| Dataset generator (exact masks, hidden regions, splits by source, provenance), labeling tool (API + real browser), training loop, FP16/accumulation logic, ONNX export parity, evaluation | Real-art quality: models were trained on synthetic data only, on a CPU; no real art was available |
| Host logic against a fake After Effects (Add, Restore, Apply, autosave, recovery, bridge: 53 tests) | The Windows installer, signing, and install on a clean machine |
| Panel logic (79 tests) and the real panel UI in Chromium against the real service (42 checks) | The panel inside After Effects (CEP), full-screen window behaviour |
| Frozen (PyInstaller) service runs `--selftest` with no Python available, on Linux | The same on Windows |

## License

Product license: not chosen yet ([LICENSE.md](LICENSE.md)). Third-party components: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

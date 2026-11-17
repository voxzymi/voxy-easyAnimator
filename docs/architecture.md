# Architecture

## Three components, one shared format

| Component | Language | Does | Never does |
|---|---|---|---|
| **AE host** (`ae_host/`) | C++ (After Effects SDK) + HTML panel | Reads the selected layer, exports its frame, creates groups/layers/masks/parents, replaces a layer's source, saves, groups each change into one undo step. | Run a model. |
| **ML service** (`service/`) | Python, packaged with PyInstaller | Runs the general model, the hair model, background fill, matting and the upscaler. Returns manifests. | Touch the After Effects project. |
| **Training pipeline** (`training/`) | Python + PyTorch | Makes datasets, labels, trains both models separately, exports ONNX, evaluates. | Run inside the product. |

The **only** shared format is the [manifest](manifest.md). The panel keeps the manifest's parts in memory until the user presses Add to Project.

## The AE host in layers

```
panel (JS)  ->  bridge (HTTP, token)  ->  Commands  ->  AeApi (abstract)  ->  AeApiSdk (After Effects SDK)   [not compiled here]
                                                 \->  FakeAe (tests)                                         [tested here]
```

Everything above `AeApi` is portable C++ and is unit tested against an in-memory fake After Effects (`ae_host/tests`, 54 tests):
the plan (which parts, which order, which parents), Add to Project, Restore, Apply, save, autosave, crash recovery, the bridge and
the service supervisor. Only `aegp/ae_api_sdk.cpp` (the thin SDK adapter) is unverified.

### Add to Project (what is created)

For a Make Layers result and the user's open eyes:

1. A project folder `Voxy - <layer name>`.
2. **Precomp mode (default):** one precomposition per group that has a visible part, named after the group (`Head`, `Face`, ...). It has the
   source composition's size, so part coordinates are identical. Each part is a layer inside it; the group's precomp layer sits in your
   composition. Groups stack back to front: Legs, Torso, Arms, Head, Hidden, Face, Hair (parts are a partition of the visible pixels, so
   this only matters for hidden parts, which must sit above the Head and below the Hair).
   **Folder mode:** one project folder per group holding the footage; all part layers are in your composition.
3. Each part layer: the part's PNG as source, **anchor = pivot** (at a joint), position = pivot in composition space, a mask from the
   part's outline (holes subtracted), label colour by group.
4. **Parenting** (a rig hierarchy): torso at the root; head, arms and legs on the torso; hands on arms; feet on legs; face parts, hair and
   hidden parts on the head; iris and eyelashes on their eye. A hidden part's children climb to the nearest part that is added. In
   precomp mode, cross-group parenting is done between the group layers (Face to Head, Head to Torso). Parenting keeps every
   layer exactly where it looked.
5. The original layer is never modified. A "Background" layer (the redrawn plate) goes at the bottom when its eye is open.
6. All of it is **one undo group**. If anything fails half way, what was made is removed again.
7. A journal (`journal.json`) records every layer, comp, footage and folder that was created, with names.

**Restore** removes exactly the last Add's layers and items that still exist with the same name, and only items that are empty/unused.
Layers you renamed, layers you added inside a group, and footage you reused elsewhere are kept. "Replace the single Hair layer" switches
the old Hair layer off (recorded in the journal) so Restore can switch it back on.

### Upscale, Apply, Discard

The service upscales the one exported frame (tiles + out-of-memory retries) into a temporary folder. Nothing in the project changes. **Apply**
first copies the picture next to the project (`Voxy Upscaled/`), because After Effects links to files, then replaces only that layer's
source and compensates so it looks identical: anchor and mask vertices x2, Scale (and every Scale keyframe) /2. **Discard** deletes the
temporary file. Temporary files also go when the project closes, at service start, and at shutdown.

### Autosave and crash recovery

- The panel runs a timer (default 5 minutes). The plug-in saves a **copy** to `Voxy Autosave/` next to the project under a temporary
  name, checks it (non-empty, `.aep` header), then renames it into place and keeps the newest ten. It never writes the project file.
- If saving the copy changes the project's path, or touches the project file, autosave switches itself off with a message. (This guards
  the one SDK call whose behaviour could not be verified: `AEGP_SaveProjectToPath`.)
- A session lock file marks an open panel. If it is found with a dead process id, the newest backup that is **newer than the last
  manual save** is offered, in plain words. Only backups Voxy wrote can be opened.

## The two models

1. **General dissection model**: U-Net with heads for part segmentation (22 classes, **all hair, front and back, one class**), 15 key
   points, full-extent ("amodal") masks of head/eyes/mouth, and a completion image for covered parts. Groups come from a fixed class to
   group table (`service/voxy_service/schema.py`); there is no group head. Hidden parts = amodal extent minus visible extent, where
   something else covers it, filled from the completion.
2. **Hair subdivision model**: U-Net that sees only the hair crop and mask and predicts the strand **flow field** and **root
   points**. The split itself is geometry (`voxy_service/hair/subdivide.py`): trace flow lines from the roots, give every hair pixel
   to the nearest line, smooth. The sections cover the Hair mask exactly, so **Combine** is the union and returns the original.

They have separate code, data, weights, versions and training runs (never in parallel: a lock enforces one training job). The service
runs the general model first for Make Layers; the hair model runs only on request.

Failure rules (tested): general model fails -> Make Layers reports it and shows **no parts**. Hair model fails -> the single Hair part is
still there and the manifest records the failure. Background or matting fail -> parts are still returned (`partial`).

## Local data

`%LOCALAPPDATA%\VoxyEasyAnimator`: `bridge.json`, `service.json`, `settings.json`, `journal.json`, `autosave_index.json`, `session.lock`,
`install.json`, `logs/` (`voxy-service.log`, `panel.log`), `jobs/`, `tmp/` (temporary upscale results and panel pictures), `upscaled/`
(for unsaved projects). Nothing leaves the computer; there is no account and no usage data.

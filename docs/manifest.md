# Manifest (schema version 1.0)

The manifest is the **only** format shared between the AE host and the ML service. The service
builds one for every job, validates it against [`manifest.schema.json`](../service/voxy_service/assets/manifest.schema.json)
plus the rules below, and only then returns it. The host (and the panel) treat it as read-only data.

Developer-facing document: the words "manifest", "ONNX" and "AEGP" never appear in the panel.

All coordinates are pixels in the **source picture** the host exported (origin top-left, y down).

## Top level

| Field | Meaning |
|---|---|
| `schema_version` | Always `"1.0"`. |
| `job` | Which job this is (see below). |
| `source` | `width`, `height` and `sha256` of the picture the job was given (`null` if it could not be read). |
| `models` | Per stage (`general`, `hair`, `matting`, `inpaint`, `upscaler`): `name`, `version`, `backend` (`stub`, `onnx`, `ncnn`, `algorithm`) and `device` (`cuda`, `cpu`, `vulkan`, `n/a`). `null` when the stage did not run. |
| `parts` | The parts, **in manifest order**: group order first, then class order. The panel lists each group's parts in this order. |
| `groups` | `{name, parts[]}` for each group that has at least one part, in group order. Never lists an empty group. |
| `skipped` | Parts with no detected pixels: `{id, name, group, reason}`; `reason` is always `no_pixels`. They are listed in the report, never shown as empty parts. |
| `keypoints` | One entry per joint (eyes, mouth, shoulders, elbows, wrists, hips, knees, ankles): `name`, `x`, `y` (pixels) and `score` (0..1, how sure the model was). |
| `hair` | `hair.subdivision`: `status` (`not_run`, `ok`, `failed`), `count`, `smoothing`, `pivot`, `error`. |
| `background` | `null`, or the redrawn background plate: `name` (always `Background`), `image`, `method` (`model` or `patch`), `bbox`, `z_order`, `produced_by`. |
| `upscale` | `null`, or the upscale result: `status`, `image` (a temporary file), `scale`, `model`, `tile`, `input_size`, `output_size`, `attempts`, `error`. |
| `errors` | Friendly errors (see below). A `failed` job always has at least one. |
| `warnings` | Friendly warnings, for example when the slow (CPU) mode is in use. |
| `timings_ms` | Milliseconds spent per step. |

### `job`

| Field | Meaning |
|---|---|
| `id` | Job id. |
| `type` | `make_layers`, `subdivide_hair`, `combine_hair`, `upscale` or `redraw_background`. |
| `status` | `ok`, `partial` (the main result exists but a secondary stage failed), `failed`, `cancelled`. |
| `created_at`, `finished_at` | UTC timestamps. |
| `parent_job_id` | For `subdivide_hair`, the `make_layers` job; for `combine_hair`, the `subdivide_hair` job. |
| `output_dir` | Folder holding the job's files. Every file path in the manifest is relative to it. |

## Parts

| Field | Meaning |
|---|---|
| `id` | Stable id, lowercase letters, digits and `_` (`head`, `eye_l`, `hair`, `hair_03`, `hidden_eye_l`). |
| `name` | Name shown to the user and used for the layer in After Effects. |
| `group` | **One of `Head`, `Face`, `Hair`, `Torso`, `Arms`, `Legs`, `Hidden`.** Returned for every part. Derived from the part's class by the fixed mapping below. |
| `class` | The part class (`head`, `eye_l`, ... , `hidden_face`). |
| `kind` | `visible`, `hidden` (a completed part that was covered) or `subdivision` (a hair strand section). |
| `z_order` | Stacking order. 0 is the back; larger is nearer the viewer. |
| `bbox` | `[x, y, width, height]` of the part inside the source picture. |
| `image` | RGBA PNG cropped to `bbox`. Only the part's pixels are opaque. |
| `mask` | 8-bit PNG cropped to `bbox` (255 = part). |
| `outline` | Polygons for the After Effects layer mask. Each has `points` (`[[x, y], ...]`) and `hole`; polygons with `hole: true` are subtracted. Source-picture coordinates. |
| `pivot` | `[x, y]` anchor point, at the joint the part rotates around. Source-picture coordinates. |
| `pixel_count` | Pixels in the mask. Always at least 1. |
| `parent` | For subdivisions, the id of the Hair part they were cut from; otherwise `null`. |
| `confidence` | 0..1, how sure the model was (1.0 for stubs). |
| `produced_by` | `{model, version, backend}`: `general` or `hair`. |

### Fixed class → group mapping

| Group | Classes |
|---|---|
| Head | `head` |
| Face | `eye_l`, `eye_r`, `iris_l`, `iris_r`, `eyelash_l`, `eyelash_r`, `eyebrow_l`, `eyebrow_r`, `nose`, `mouth` |
| Hair | `hair` (one part: front and back hair together) |
| Torso | `torso` |
| Arms | `arm_l`, `arm_r`, `hand_l`, `hand_r` |
| Legs | `leg_l`, `leg_r`, `foot_l`, `foot_r` |
| Hidden | `hidden_face`, `hidden_eye_l`, `hidden_eye_r`, `hidden_mouth` |

`_l` / `_r` are the **character's** left and right (the character's left is on the right of the picture).

## Errors and warnings

```json
{
  "code": "GENERAL_MODEL_FAILED",
  "stage": "general",
  "user_message": "Voxy could not cut your character into parts.",
  "what_to_do": "Try a picture with one character and a plain background. ...",
  "help_topic": "make-layers-failed",
  "recoverable": true,
  "detail": "short technical note for the log, never shown in the panel"
}
```

`user_message` says what happened, `what_to_do` says what the user can do, `help_topic` opens the right
part of the help page. `stage` is the pipeline step that failed, `recoverable` says whether trying again can help,
and `detail` is a short technical note for the log (never shown in the panel).
Warnings carry `code`, `user_message`, `what_to_do` and `help_topic`.
Stack traces are written to the service log file only.

## Rules the validator enforces

1. The document matches the JSON Schema; `group` is present on every part and is one of the seven names.
2. A `visible` or `hidden` part's group equals the fixed mapping for its `class`.
3. Part ids are unique; no part has zero pixels (those go in `skipped`); no id is both a part and skipped.
4. `groups` lists exactly the groups that have parts, in group order, with part ids in `parts` order.
5. **Make Layers:** the Hair group holds exactly one part (named `Hair`, `kind: visible`); there are no subdivisions.
6. **Subdivisions** are in the Hair group, have a `parent`, never appear next to the single Hair part, and share one `pivot`.
7. A `failed` Make Layers job shows **no parts**, and a `failed` job carries at least one error.
8. When `hair.subdivision.status` is `failed` the single Hair part is still listed (job status `partial`) and the error is recorded.
9. When files are checked, every `image` and `mask` exists.

## Job types

| `type` | Request fields (besides `type`) | Result |
|---|---|---|
| `make_layers` | `image_path` / `image_b64` / multipart `image`; `options.background_removal`, `options.background_fill` (`auto`, `model`, `patch`) | All parts, groups, keypoints; a `background` plate when Background Removal is on. |
| `subdivide_hair` | `parent_job_id`; `options.count`, `options.smoothing` | The Hair group's subdivisions (or the single Hair part if the hair model failed). |
| `combine_hair` | `subdivision_job_id` | The single Hair part again (the union of the subdivisions). |
| `upscale` | `image_path` (the one selected layer's exported frame) | `upscale` block; the result is a temporary file until Apply or Discard. |
| `redraw_background` | `image_path`, `mask_path`; `options.fill` | `background` block. |

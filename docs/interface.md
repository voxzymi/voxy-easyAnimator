# Local interfaces

Three programs talk over two local interfaces. Both are plain HTTP on `127.0.0.1` with a per-session token. The only shared
data format is the [manifest](manifest.md).

```
 panel (HTML)  --- bridge (After Effects commands) --->  AE plug-in (C++)       never runs models
     |
     +----------- service (cutting jobs)  --------->  ML service (Python)      never touches the project
```

Both servers listen on `127.0.0.1` only, require the `X-Voxy-Token` header (except `/v1/health` and the bridge's `/v1/ping`),
reject any `Host` header that is not `127.0.0.1`/`localhost`, and send CORS headers so the panel (a `file://` page) can call them.
Connection details are written to files in the data folder (`%LOCALAPPDATA%\VoxyEasyAnimator`): `bridge.json` (AE plug-in) and
`service.json` (service): `{port, token, pid}`.

## ML service (`python -m voxy_service`, or `voxy-service.exe`)

| Call | Meaning |
|---|---|
| `GET /v1/health` | `{status, service_version, api_version, manifest_schema, models{general,hair,matting,inpaint: backend, version, available, loaded}, device{cuda_available, in_use, warnings}, busy, stub_models, uptime_s, log_path}`. No token needed. |
| `GET /v1/preflight` | Setup checks in plain words: `{overall: ok|warn|fail, checks:[{id,status,title,message,what_to_do}]}`. |
| `POST /v1/jobs[?wait=1&timeout=600]` | Start a job. Body is JSON `{type, ...}` (see below) or `multipart/form-data` with parts `request` (JSON) and `image` (PNG). Returns `202 {job_id,status}`, or the finished job with `wait=1`. |
| `GET /v1/jobs/<id>[?wait=1&timeout=60]` | `{job_id, type, status: queued|running|done|cancelled, step, manifest?}`. |
| `POST /v1/jobs/<id>/cancel` | Cooperative cancel (kills the upscaler process if it is running). |
| `POST /v1/jobs/<id>/discard` | Delete the job's folder (an upscale result's temporary file). |
| `GET /v1/jobs/<id>/files/<path>[?token=]` | A file from the job's folder. Only picture files accept the token as a query parameter (for `<img>` tags). Paths cannot leave the job folder. |
| `POST /v1/tmp/cleanup` | Delete every temporary upscale result (called when a project closes; also done at start-up and shutdown). |
| `GET/PUT /v1/settings` | The panel's settings, kept in `settings.json` (the uninstaller asks before deleting it). |
| `POST /v1/shutdown` | Stop the service. |

Job types and what they return are in [manifest.md](manifest.md#job-types). Rules:

- Jobs run **one at a time** (the GPU is shared). Every step has a timeout (`timeouts_s` in the config); a step that runs over is
  abandoned, the job is cancelled, and the manifest carries `STEP_TIMEOUT`.
- Errors are friendly objects (`user_message`, `what_to_do`, `help_topic`). Raw errors and stack traces go to
  `logs/voxy-service.log`.
- Command line: `--selftest` (start, health check, cut the sample, exit 0/1), `--preflight [--plain]`, `--parent-pid <pid>` (exit when
  After Effects exits), `--data-dir`, `--info-file`, `--config`.

## AE bridge (inside the After Effects plug-in)

`POST /v1/cmd` with `{ "cmd": "<name>", ...arguments }` returns `{ok:true, result}` or `{ok:false, error:{code,user_message,what_to_do,help_topic,detail}}`.
Every command except `ensure_service` runs on After Effects' main thread (the plug-in's idle hook pumps a queue); if After Effects is
busy for 60 s the caller gets `TIMEOUT` and the command is never run later.

| Command | Arguments | Result / effect |
|---|---|---|
| `ping` | | `{pong:true}` |
| `ensure_service` | | Starts the ML service if needed; `{port, token, pid, started}`. |
| `selection` | | The selected layer and composition (`has_layer`, `layer_id`, `layer_name`, `source_kind`, `signature`, `project_id`, ...). |
| `export_frame` | `path`, optional `layer_id`, `time` | Writes that layer's frame to a PNG. |
| `add_to_project` | `manifest`, `output_dir`, `visible_ids[]`, `options{container: precomp|folder, replace_hair, include_background}` | Adds the visible parts. **One undo step.** Journals what it made. |
| `restore` | | Removes what the last Add made (and only that). `more` says whether an earlier Add can still be restored. |
| `apply_upscale` | `layer_id`, `image_path`, `factor` | Keeps a durable copy next to the project, then swaps that layer's source. **One undo step.** |
| `save_project` | | Saves; `{saved, time}` (`cancelled` if the first Save As is cancelled). |
| `autosave` | | `{status: saved|skipped|failed|disabled, backup, time}`; see [architecture.md](architecture.md#autosave-and-crash-recovery). |
| `session_begin` / `session_end` | | Crash detection: `{recovery: {backup, explanation, ...} | null}`. |
| `recovery_find` / `recovery_open` / `backups` / `autosave_reset` | | Recovery helpers. `recovery_open` only opens backups Voxy itself wrote. |

The host's contract with After Effects is the abstract `AeApi` in `ae_host/core/ae_api.h` (documented there).

# Install test checklist

**No Windows machine, no Inno Setup, and no After Effects were available in development.** Nothing below was run on Windows.
What *was* checked is listed so you know what is left.

| # | Scenario | Status | What to look for |
|---|---|---|---|
| 1 | **Clean Windows machine with no Python installed.** | ⚠️ **Needs manual testing.** Partly verified: the PyInstaller build of the service runs `--selftest` (health check + cutting the sample into 24 parts) and `--preflight` on Linux with an **empty environment and no Python on the path**. The Windows build and the installer were not built. | After install, the Checking-your-setup page shows OK lines and "Voxy starts and cuts the sample character". First Make Layers works without a terminal. |
| 2 | **Machine with an unsupported GPU (or none).** | ⚠️ Needs manual testing. Verified: CPU fallback path in tests (ONNX Runtime session on CPU, `GPU_NOT_AVAILABLE` warning in the manifest and the panel's slow-mode banner), preflight wording. | Installer says "Update your NVIDIA driver to use the fast mode. The slow mode will still work." Panel shows the slow-mode note. Make Layers still works. |
| 3 | **Machine with a supported GPU (RTX 3060 12 GB).** | ⚠️ Needs manual testing. Nothing GPU-related could be run. | "Your graphics card is supported." Make Layers uses CUDA (`device.in_use` = cuda in `/v1/health`). Upscale works (Vulkan). Measure memory with `measure_memory.py`. |
| 4 | **Install over an older version.** | ⚠️ Needs manual testing. The script uses a fixed `AppId`, so Inno Setup upgrades in place. | Files replaced; settings and journal kept; plug-in copied again; no duplicate Start menu entries. |
| 5 | **Uninstall.** | ⚠️ Needs manual testing. The uninstall code removes the plug-in copies it recorded in `installed_plugins.txt` and asks before deleting `%LOCALAPPDATA%\VoxyEasyAnimator`. | Choosing No keeps settings and untitled-project backups; backups next to projects are never deleted; panel and plug-in gone from After Effects. |
| 6 | **Install with After Effects open.** | ⚠️ Needs manual testing. The wizard checks for `AfterFX.exe` and offers Retry/Cancel. | Cannot continue until After Effects is closed. |
| 7 | **Detecting After Effects versions and install folders.** | ❌ Not verified. Registry keys and folders are from memory (`installer/ae_locations.json`, `verified: false`). | The right folders are found for each supported version; unsupported versions are not offered. |
| 8 | **Windows SmartScreen.** | ⚠️ Unsigned build: expect the warning. Signing needs a certificate (decision #2). | After signing, no strong warning. |
| 9 | **License page.** | ⚠️ `build/license_and_notices.txt` is generated from `LICENSE.md` and `THIRD_PARTY_NOTICES.md`; the page itself was not shown. | Product license and third-party notices (including upscayl-ncnn's AGPL-3.0) are shown. |
| 10 | **No network use.** | ✅ Nothing in the service, bridge or panel contacts the internet (listeners are `127.0.0.1` only; there is no telemetry code). ⚠️ Confirm with a firewall log during a full session. | No outbound connections. |

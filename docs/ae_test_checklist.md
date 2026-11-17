# After Effects manual test checklist

**After Effects could not be run in the development environment**, and the AEGP plug-in (`ae_host/aegp`) has never been compiled. This
list says, for every required behaviour, what **was verified** (and how) and what **needs a person with After Effects**.

Legend: ✅ verified in development · ⚠️ needs a manual test in After Effects · ❌ cannot be verified without the SDK/AE

How things were verified here: **fake AE** = the C++ host core tested against an in-memory After Effects (54 tests, `ctest`);
**panel logic** = the panel controller with mock host and service (79 Node tests); **browser** = the real panel in headless Chromium
against the real service and a stand-in bridge (42 checks); **service** = the Python service tests.

## A. Do these first (they decide whether the rest can work)

| # | Check | Status |
|---|---|---|
| A1 | Build the plug-in: `cmake -DVOXY_BUILD_AEGP=ON -DAE_SDK_DIR=...` and fix compile errors in `aegp/ae_api_sdk.cpp` (lines marked `[verify]`). | ❌ |
| A2 | After Effects loads the plug-in (no error at start-up) and `bridge.json` appears in `%LOCALAPPDATA%\VoxyEasyAnimator`. | ❌ |
| A3 | **`AEGP_SaveProjectToPath` semantics.** Autosave once, then check **File > Save**: does the project still point at your file, or at `Voxy Autosave\...-autosave-....aep`? Voxy switches autosave off and says why if the path changed, but confirm which happens. | ❌ |
| A4 | The panel appears under **Window > Extensions > Voxy EasyAnimator** (CEP, unsigned: needs `PlayerDebugMode`, see installer). | ⚠️ |
| A5 | The CEF page can reach `http://127.0.0.1:<port>` of the bridge and the service (CORS is set; confirm in the panel's developer tools). | ⚠️ |
| A6 | `AEGP_ExecuteScript` runs the ExtendScript snippets (masks, parenting with jump, replace source, label, usage counts). | ❌ |
| A7 | Exported frame has correct alpha/colours (premultiplication). | ❌ |

## B. The task's checklist, in order

| # | Step | Verified in development | Needs a manual test |
|---|---|---|---|
| B1 | Open the panel with a layer selected: the preview loads without pressing anything. | ✅ panel logic, browser (preview of the selected layer appears on open) | ⚠️ real frame export from AE |
| B2 | Change the selection: the preview updates; going back does not reprocess. | ✅ panel logic (cache hit counted: no second export), browser polling | ⚠️ real selection polling |
| B3 | Expand to full screen and restore. | ✅ logic + browser (class toggled, remembered, session restored) | ⚠️ the floating window opens at screen size; docked panel closes; Normal size reopens it |
| B4 | Make Layers: parts appear in the panel but **not in the project**. | ✅ panel logic and browser: no host command that changes the project is sent | ⚠️ confirm the timeline is unchanged |
| B5 | Hide a part with its eye: the timeline does not change. | ✅ no host call at all is made by eye buttons | ⚠️ |
| B6 | Hide a whole group: the group eye shows the right state (open / half-open / closed). | ✅ logic + browser | |
| B7 | Add to Project: one group per group name, masked layers inside, **a single Hair layer**. | ✅ fake AE (names, masks, anchors = pivots, parenting keeps positions, one undo group) | ⚠️ real layers, masks and precomps look right; check mask edges |
| B8 | Hidden parts are not added; empty groups are not created. | ✅ fake AE + panel logic | ⚠️ |
| B9 | The eye state does not change visibility in the project. | ✅ no host command; the project is never told | ⚠️ |
| B10 | Select the Hair group, run **Cut hair into subdivisions**: sections appear in the panel; Add to Project adds them in the Hair group (replace is optional). | ✅ service geometry, panel logic, browser, fake AE | ⚠️ real result with trained hair model |
| B11 | Run **Combine**: back to one Hair part. | ✅ service (union equals original), panel logic, browser | |
| B12 | Select a layer, run **Upscale 2x**. | ✅ service with a stand-in upscaler (tiles, OOM retry, timeout, cancel), browser | ⚠️ real `upscayl-ncnn` on the RTX 3060 and a Vulkan driver |
| B13 | The project is unchanged until Apply in Project. | ✅ panel logic and browser | ⚠️ |
| B14 | Apply in Project; then **undo it**. | ✅ fake AE: one undo group; anchor, masks, scale and keyframes compensated; only that layer changes | ⚠️ real undo restores the old source; layer looks identical |
| B15 | Discard. | ✅ temp file deleted, no project command | |
| B16 | Save Project. | ✅ fake AE (time shown, no questions) | ⚠️ real save; first Save As dialog |
| B17 | Wait for an autosave: a message appears, a backup is next to the project, your saved file is untouched. | ✅ fake AE (copy, validation, never writes the project, keeps ten, failure leaves earlier files alone) | ⚠️ see A3 |
| B18 | Force-quit After Effects; reopen: the panel offers the backup. | ✅ logic (lock + newest backup newer than last save) | ⚠️ with a real crash (Task Manager > End task) |
| B19 | Restore removes only the last Add's layers. | ✅ fake AE (user layers, renamed layers, reused footage, user layers in a group are kept) | ⚠️ |

## C. Extra checks worth doing

- Open the project on a **different composition size** after Make Layers: Add to Project must refuse (`SIZE_MISMATCH`).
- Select a precomp or video layer: Upscale must refuse with a clear message.
- Add twice, Restore twice: the second Restore removes the first Add.
- Kill the service process while the panel is open: the panel shows the friendly "helper program is not running" message.
- Panel size, full screen, group, toggles and autosave interval come back after restarting After Effects.

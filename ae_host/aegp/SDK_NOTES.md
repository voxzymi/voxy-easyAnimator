# AEGP adapter: what is verified and what is not

`ae_api_sdk.cpp` and `entry.cpp` were written **without the After Effects SDK headers** (they cannot be redistributed
and the build environment could not download them). They have **never been compiled**. Everything above the `AeApi`
interface (Add to Project, Restore, Apply, autosave, bridge, supervisor) *is* compiled and tested against a fake.

Lines marked `// [verify]` use an SDK function whose name, suite version or signature should be checked against your
SDK's headers. Expect compile errors on first build; fix them in `ae_api_sdk.cpp` only.

| Need | Used | Confidence |
|---|---|---|
| Entry point, idle hook, death hook | `EntryPointFunc`, `AEGP_RegisterIdleHook`, `AEGP_RegisterDeathHook` | high |
| Undo groups | `AEGP_StartUndoGroup` / `AEGP_EndUndoGroup` | high |
| Selected layer | `AEGP_GetMostRecentlyUsedComp`, `AEGP_GetNewCollectionFromCompSelection`, collection suite | medium |
| Frame export | layer render options + `AEGP_RenderAndCheckoutLayerFrame`, world suite, `stb_image_write` | medium (premultiplied alpha to verify) |
| Create folder / comp / import / add layer | `AEGP_CreateNewFolder`, `AEGP_CreateComp`, `AEGP_NewFootage`+`AddFootageToProject`, `AEGP_AddLayer` | medium |
| Anchor / position | stream suite `AEGP_SetStreamValue` | medium-high |
| Layer on/off, delete | `AEGP_SetLayerFlag(VIDEO_ACTIVE)`, `AEGP_DeleteLayer`, `AEGP_DeleteItem` | high |
| Masks, parenting that keeps position, label, replace source, item use counts | **ExtendScript through `AEGP_ExecuteScript`** (`Shape`, `setParentWithJump`, `replaceSource`, `usedIn`) | high for the JavaScript, medium for `AEGP_ExecuteScript`'s signature |
| Save | `AEGP_SaveProjectToPath` | **semantics unverified**: it may behave like Save As. Autosave detects that and switches itself off. First thing to test. |

ExtendScript is Adobe's older scripting engine; it still runs inside After Effects. If Adobe removes it, these few
operations move to the `AEGP_MaskSuite`/`AEGP_MaskOutlineSuite`/`AEGP_LayerSuite` equivalents.

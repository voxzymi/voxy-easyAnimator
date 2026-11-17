# Decisions needed from the project owner

Nothing below was decided without you. Each item says what was done in the meantime and what it blocks.

| # | Decision | Done meanwhile | Blocks |
|---|---|---|---|
| 1 | **Supported After Effects versions.** | `installer/supported_ae_versions.json` holds a DRAFT list (2022 to 2026) with `"confirmed": false`. `build_windows.ps1` refuses a release build until you set it to `true`; the install guide and the panel's CEP host range (`ae_host/panel/CSXS/manifest.xml`, `[22.0,99.9]`) use the same draft. | Release installer, INSTALL.md wording. |
| 2 | **Code-signing certificate.** | The installer builds unsigned (a `-Sign` switch is ready). INSTALL.md explains the Windows SmartScreen warning. The panel is also unsigned: it needs either a signed ZXP (cert + `ZXPSignCmd`) or `PlayerDebugMode=1` (set by the installer only with `-UnsignedPanel`, not for public release). | Public release without warnings. Buying a certificate is your call. |
| 3 | **Sample character.** | I created an original one (`service/voxy_service/assets/sample_character.png`, CC0, drawn by this project's code). | Replace it if you want your own art. |
| 4 | **Product license.** | `LICENSE.md` is a placeholder (all rights reserved). | Anything public. |
| 5 | **AGPL-3.0 upscayl-ncnn.** | Run as a separate program; obligations listed in `THIRD_PARTY_NOTICES.md`. | Bundling it. Please confirm you accept AGPL's source-offer duty for the shipped binary (or choose a different upscaler). |
| 6 | **Training data.** | `data/raw/` was empty. The generator draws its own CC0 characters and only uses reference sheets that have a provenance row with a known license or owner permission. | Real-art quality: send the reference sheets with their licenses. |
| 7 | **Real-art model quality.** | Models were trained on synthetic data only, on a CPU. | Release quality. Needs training on the RTX 3060 plus a corrected real-art set. |
| 8 | **Panel technology.** | CEP (HTML) panel behind a thin adapter; UXP migration is a swap of `adapters/cep.js`. See `docs/panel-technology.md`. | Confirm, especially because I could not read Adobe's own announcement. |
| 9 | **Download page URL** for `docs/README-public.md`. | `DOWNLOAD_URL_TO_BE_SET` placeholder. | Public page. |
| 10 | **Windows version target.** | Windows 10/11 64-bit assumed. | INSTALL.md wording. |
| 11 | **Background layer placement.** | The redrawn background is added as one "Background" layer at the bottom of the composition, outside the groups. | Say if you want it inside a group. |
| 12 | **"Replace the single Hair layer".** | Replace switches the old Hair layer off (not delete) so Restore can bring it back. | Say if you want it deleted. |

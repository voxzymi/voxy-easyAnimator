# data/raw: source reference images

Put the reference sheets here (hair in many colours, hair split into flat pieces, separated strand shapes).

**Every file needs a row in [`PROVENANCE.csv`](PROVENANCE.csv)** with its source, author, license and
whether it may be shared. Nothing in this folder may go into a training set that will be shared unless its row
shows a known license or the owner's written permission.

| column | meaning |
|---|---|
| `file` | path relative to this folder |
| `source` | where it came from (URL or "drawn by <name>") |
| `author` | the artist / owner |
| `license` | SPDX id (`CC0-1.0`, `CC-BY-4.0`, `MIT`, ...), `owner-permission`, or `project-original`. Non-commercial licenses (`CC-BY-NC-*`) count as internal-only. |
| `license_url` | link to the license text, or where the permission is stored |
| `owner_permission` | for `owner-permission`: who gave it, when, and what for |
| `acquired` | date (YYYY-MM-DD) |
| `redistributable` | `yes` or `no`: may derived training sets be shared |
| `sha256` | hash of the file, so a swapped file is noticed |
| `notes` | anything else |

Check the log with `python -m training.shared.provenance audit`.

To use a sheet for hair pieces, add a sidecar `<name>.parts.json` next to it (see
`training/shared/sheets.py`; the labeling tool can write it). A sheet without a provenance row is refused.

**Status of this repository:** no reference sheets were present when the generator was written. The generator
therefore draws its own original characters (CC0, made by `training/shared/procedural_art.py`) and uses sheets
only when they are added here with a provenance row.

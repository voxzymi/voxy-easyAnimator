# Changelog

All notable changes to Voxy EasyAnimator are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased] - 0.1.0

First development version. Not yet released.

### Added
- **Make Layers**: cuts a flat character into parts in groups (Head, Face, Hair, Torso, Arms, Legs, Hidden) and shows them in the panel first.
- **Add to Project**: adds the visible parts as masked layers with joint pivots, one group per group name, as one undo step. **Restore** removes what the last Add made.
- **Eye buttons** for every part and group, with a half-open state for groups.
- **Hair sections** with a shared root pivot, and **Combine**.
- **Background Removal** with an AI fill or a flat-color fill.
- **Upscale 2x** with a side-by-side preview, **Apply in Project**, and **Discard**.
- **Save Project**, **autosave** backups, and crash recovery.
- A full-screen window, a first-run welcome, a sample character, and a **Test setup** button.
- A Windows installer that checks your graphics card, driver, and disk space.

### Known limits
- The After Effects plug-in could not be built or tried in the development environment. See `docs/ae_test_checklist.md`.
- The models in this version are not trained on real art yet.

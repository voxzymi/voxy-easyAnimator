# Panel technology: why a CEP HTML panel, and how to move to UXP

## What was checked

- **Searched** for Adobe's current recommendation for After Effects panels. Results (secondary sources; Adobe's own blog host
  was not reachable from the build environment, so the primary page could **not** be read):
  - CEP (HTML panels) still works in After Effects today.
  - Adobe has announced a move to **UXP** and a multi-year phase-out of CEP. One source quotes an After Effects UXP **public
    beta for November 2026**; another says CEP is disabled by default in December 2028 and removed later. An April 2026 note
    says there is no production UXP panel framework for After Effects yet.
  - The After Effects SDK guide describes native AEGP panels (the "Panelator" sample) as far more work than an HTML5 panel
    and recommends the HTML5 panel route.
- **Please verify** the roadmap on Adobe's developer site; dates differ between sources.

## Decision

A **CEP HTML panel**, because it is what works and is supported today, with all behaviour in plain JavaScript modules that know nothing
about CEP:

```
ae_host/panel/js/core/*   state, eyes, settings, controller (tested in Node, 79 tests)
ae_host/panel/js/api/*    HTTP clients for the service and the host bridge
ae_host/panel/js/ui/*     DOM drawing
ae_host/panel/js/adapters/cep.js   the ONLY file that knows about CEP (or a plain browser)
```

Moving to UXP means writing `adapters/uxp.js` (file access, open URL, window calls) and a `manifest.json`; the controller, clients and
view are reused. The panel never calls ExtendScript: every project change goes through the C++ plug-in's localhost bridge.

## Full screen

A docked CEP panel cannot grow past its dock and cannot overlay the screen. Approach used (as the task allows):

- Two CEP extensions in one bundle: the dockable **Voxy EasyAnimator** panel and a floating (Modeless) **Voxy EasyAnimator (Full
  screen)** window sized to the monitor (`resizeContent(screen.availWidth, screen.availHeight)`).
- The **Full screen** button opens the floating window and closes the docked panel; **Normal size** does the reverse.
- The work moves with the user: the Make Layers job ids, hair sections and eye states are saved (`settings.session`), and the
  service keeps the job files, so the new window restores them.

**Limits:** it is a floating window, not a true overlay; the window's position is chosen by After Effects/the OS (CEP cannot move it);
the OS maximize button works on the floating window. Not verified inside After Effects (checklist items B7 to B9).

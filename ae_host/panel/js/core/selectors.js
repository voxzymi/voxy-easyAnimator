// What the buttons and sections should do, as pure functions of the panel's state.
(function (root) {
  'use strict';
  const eyes = typeof require === 'function' ? require('./eyes.js') : root.Voxy.eyes;
  const parts = typeof require === 'function' ? require('./parts.js') : root.Voxy.parts;

  const hasParts = (s) => !!(s.make.view && s.make.view.parts && s.make.view.parts.length);
  const idle = (s) => !s.busy;
  const selectedOk = (s) => !!(s.selection && s.selection.has_layer);

  const sel = {
    canMakeLayers: (s) => idle(s) && selectedOk(s),
    canAdd: (s) => idle(s) && hasParts(s) && !s.make.sample && eyes.visibleIds(s.make.view, s.make.eyes).length > 0,
    canRestore: (s) => idle(s) && !!s.canRestore,
    canUpscale: (s) => idle(s) && selectedOk(s) && s.selection.source_kind === 'still',
    canApply: (s) => idle(s) && s.upscale.status === 'ready',
    canDiscard: (s) => idle(s) && s.upscale.status === 'ready',
    canSave: (s) => idle(s) && !!(s.selection && s.selection.has_project),
    // The hair options appear only when the Hair group is chosen in the dropdown.
    showHairOptions: (s) => hasParts(s) && s.settings.group === 'Hair',
    hairIsSubdivided: (s) => !!s.make.hairResult,
    canCutHair: (s) => idle(s) && hasParts(s) && s.settings.group === 'Hair' && !s.make.hairResult && !s.make.sample,
    canCombine: (s) => idle(s) && !!s.make.hairResult,
    groups: (s) => parts.groupsOf(s.make.view),
    groupOptions: (s) => parts.groupsOf(s.make.view).map((g) => g.name),
    currentGroup: (s) => {
      const names = parts.groupsOf(s.make.view).map((g) => g.name);
      return names.includes(s.settings.group) ? s.settings.group : names[0] || '';
    },
    groupEye: (s, name) => eyes.groupState(s.make.eyes, parts.idsInGroup(s.make.view, name)),
    partEye: (s, id) => (eyes.isHidden(s.make.eyes, id) ? 'closed' : 'open'),
    hiddenCount: (s) => (s.make.view ? eyes.hiddenCount(s.make.view, s.make.eyes) : 0),
    emptyKey: (s) => {
      const x = s.selection;
      if (!x || !x.has_project) return 'noProject';
      if (!x.has_comp) return 'noComp';
      if (x.selected_layers > 1) return 'manyLayers';
      if (!x.has_layer) return 'noLayer';
      return null;
    },
  };
  if (typeof module === 'object' && module.exports) module.exports = sel; else (root.Voxy = root.Voxy || {}).selectors = sel;
})(typeof self !== 'undefined' ? self : this);

// Eye buttons. They change ONLY what the panel previews and what Add to Project uses. They never touch the project.
(function (root) {
  'use strict';
  const BACKGROUND_ID = 'background';

  const empty = () => ({ hidden: {} });                               // cleared whenever Make Layers runs again
  const isHidden = (eyes, id) => !!eyes.hidden[id];

  function setMany(eyes, ids, hide) {
    const hidden = Object.assign({}, eyes.hidden);
    ids.forEach((id) => { if (hide) hidden[id] = true; else delete hidden[id]; });
    return { hidden };
  }
  const toggle = (eyes, id) => setMany(eyes, [id], !isHidden(eyes, id));

  // 'open' (all shown), 'closed' (all hidden) or 'half' (some hidden).
  function groupState(eyes, ids) {
    if (!ids.length) return 'open';
    const n = ids.filter((id) => isHidden(eyes, id)).length;
    return n === 0 ? 'open' : n === ids.length ? 'closed' : 'half';
  }
  // One press: everything shown -> hide all; otherwise (half or closed) -> show all.
  function toggleGroup(eyes, ids) { return setMany(eyes, ids, groupState(eyes, ids) === 'open'); }

  const icon = (state) => (state === 'open' ? 'eye-open' : state === 'half' ? 'eye-half' : 'eye-closed');

  // Ids Add to Project will use: manifest order, minus hidden parts; "background" when it exists and is shown.
  function visibleIds(manifest, eyes) {
    const ids = (manifest.parts || []).filter((p) => !isHidden(eyes, p.id)).map((p) => p.id);
    if (manifest.background && !isHidden(eyes, BACKGROUND_ID)) ids.push(BACKGROUND_ID);
    return ids;
  }
  const hiddenCount = (manifest, eyes) => (manifest.parts || []).filter((p) => isHidden(eyes, p.id)).length;

  // Subdividing the hair: the sections start with the same eye state as the Hair part they came from.
  function afterSubdivide(eyes, subIds) {
    const hairHidden = isHidden(eyes, 'hair');
    const next = setMany(eyes, ['hair'], false);
    return setMany(next, subIds, hairHidden);
  }
  // Combining: the Hair part is hidden only if every section was hidden.
  function afterCombine(eyes, subIds) {
    const all = subIds.length > 0 && subIds.every((id) => isHidden(eyes, id));
    return setMany(setMany(eyes, subIds, false), ['hair'], all);
  }
  // Forget eye states of parts that are no longer in the manifest.
  function prune(eyes, manifest) {
    const keep = new Set((manifest.parts || []).map((p) => p.id).concat(manifest.background ? [BACKGROUND_ID] : []));
    const hidden = {};
    Object.keys(eyes.hidden).forEach((id) => { if (keep.has(id)) hidden[id] = true; });
    return { hidden };
  }

  const api = { BACKGROUND_ID, empty, isHidden, toggle, setMany, groupState, toggleGroup, icon, visibleIds, hiddenCount, afterSubdivide, afterCombine, prune };
  if (typeof module === 'object' && module.exports) module.exports = api; else (root.Voxy = root.Voxy || {}).eyes = api;
})(typeof self !== 'undefined' ? self : this);

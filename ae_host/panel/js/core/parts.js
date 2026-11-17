// Helpers for the parts a Make Layers result holds. Parts are listed under their group in manifest order.
(function (root) {
  'use strict';

  function groupsOf(manifest) {
    if (!manifest) return [];
    const byId = {};
    (manifest.parts || []).forEach((p) => { byId[p.id] = p; });
    return (manifest.groups || []).map((g) => ({ name: g.name, parts: g.parts.map((id) => byId[id]).filter(Boolean) }));
  }
  const partsInGroup = (manifest, name) => (groupsOf(manifest).find((g) => g.name === name) || { parts: [] }).parts;
  const idsInGroup = (manifest, name) => partsInGroup(manifest, name).map((p) => p.id);

  // The Hair group of the panel's view: the single Hair part, or its sections after "Cut hair".
  function rebuildGroups(parts) {
    const order = ['Head', 'Face', 'Hair', 'Torso', 'Arms', 'Legs', 'Hidden'];
    return order.map((n) => ({ name: n, parts: parts.filter((p) => p.group === n).map((p) => p.id) })).filter((g) => g.parts.length);
  }

  // The manifest the panel shows: the Make Layers result, with the Hair group swapped for the sections when they exist.
  function viewManifest(base, hairResult) {
    if (!base) return null;
    if (!hairResult || hairResult.hair.subdivision.status !== 'ok') return base;
    const parts = base.parts.filter((p) => p.group !== 'Hair').concat(hairResult.parts);
    const order = ['Head', 'Face', 'Hair', 'Torso', 'Arms', 'Legs', 'Hidden'];
    parts.sort((a, b) => order.indexOf(a.group) - order.indexOf(b.group));   // stable: keeps manifest order inside a group
    return Object.assign({}, base, { parts, groups: rebuildGroups(parts) });
  }

  // Where a part's picture lives: files of a job are served by the service.
  function partFile(jobOf, part, key) { return { job: jobOf(part), path: part[key || 'image'] }; }

  const api = { groupsOf, partsInGroup, idsInGroup, rebuildGroups, viewManifest, partFile };
  if (typeof module === 'object' && module.exports) module.exports = api; else (root.Voxy = root.Voxy || {}).parts = api;
})(typeof self !== 'undefined' ? self : this);

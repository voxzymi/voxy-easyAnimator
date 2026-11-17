// Settings remembered between sessions: panel size, full screen, group, toggles, autosave interval.
(function (root) {
  'use strict';
  const KEY = 'voxy.settings.v1';
  const DEFAULTS = {
    panel: { width: 420, height: 760 },
    fullScreen: false,
    group: 'Head',
    backgroundRemoval: false,
    backgroundFill: 'auto',                 // auto | model | patch
    container: 'precomp',                   // precomp | folder
    hairCount: 6,
    hairSmoothing: 0.3,
    replaceHair: false,
    lowMemory: false,                       // upscale setting
    autosaveMinutes: 5,                     // 0 = off
    welcomeDone: false,
  };
  const clampInt = (v, lo, hi, d) => { const n = Math.round(Number(v)); return Number.isFinite(n) ? Math.min(hi, Math.max(lo, n)) : d; };

  // Anything stored is checked again, so a damaged or old settings file cannot break the panel.
  function sanitize(raw) {
    const s = Object.assign({}, DEFAULTS, raw && typeof raw === 'object' ? raw : {});
    const p = s.panel && typeof s.panel === 'object' ? s.panel : {};
    s.panel = { width: clampInt(p.width, 240, 4000, DEFAULTS.panel.width), height: clampInt(p.height, 240, 3000, DEFAULTS.panel.height) };
    s.fullScreen = s.fullScreen === true;
    s.group = typeof s.group === 'string' ? s.group : DEFAULTS.group;
    s.backgroundRemoval = s.backgroundRemoval === true;
    s.backgroundFill = ['auto', 'model', 'patch'].includes(s.backgroundFill) ? s.backgroundFill : 'auto';
    s.container = ['precomp', 'folder'].includes(s.container) ? s.container : 'precomp';
    s.hairCount = clampInt(s.hairCount, 2, 24, DEFAULTS.hairCount);
    const sm = Number(s.hairSmoothing);
    s.hairSmoothing = Number.isFinite(sm) ? Math.min(1, Math.max(0, sm)) : DEFAULTS.hairSmoothing;
    s.replaceHair = s.replaceHair === true;
    s.lowMemory = s.lowMemory === true;
    s.autosaveMinutes = clampInt(s.autosaveMinutes, 0, 120, DEFAULTS.autosaveMinutes);
    s.welcomeDone = s.welcomeDone === true;
    return s;
  }

  // storage: { getItem, setItem } (localStorage in the panel). Every access is wrapped: it can be blocked or empty.
  function load(storage) {
    try { return sanitize(JSON.parse(storage.getItem(KEY))); } catch (e) { return sanitize(null); }
  }
  function save(storage, settings) {
    try { storage.setItem(KEY, JSON.stringify(sanitize(settings))); return true; } catch (e) { return false; }
  }

  const api = { KEY, DEFAULTS, sanitize, load, save };
  if (typeof module === 'object' && module.exports) module.exports = api; else (root.Voxy = root.Voxy || {}).settings = api;
})(typeof self !== 'undefined' ? self : this);

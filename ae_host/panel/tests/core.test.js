const test = require('node:test');
const assert = require('node:assert/strict');
const eyes = require('../js/core/eyes.js');
const parts = require('../js/core/parts.js');
const settings = require('../js/core/settings.js');
const PreviewCache = require('../js/core/previewCache.js');
const strings = require('../js/core/strings.js');
const errors = require('../js/core/errors.js');
const { fixture } = require('./helpers.js');

// ------------------------------------------------------------------ eyes
test('a group eye is open, half-open or closed depending on its parts', () => {
  const ids = ['a', 'b', 'c'];
  let e = eyes.empty();
  assert.equal(eyes.groupState(e, ids), 'open');
  e = eyes.toggle(e, 'b');
  assert.equal(eyes.groupState(e, ids), 'half');                      // only some parts hidden: half-open
  assert.equal(eyes.icon('half'), 'eye-half');
  e = eyes.toggle(eyes.toggle(e, 'a'), 'c');
  assert.equal(eyes.groupState(e, ids), 'closed');
  assert.equal(eyes.icon('closed'), 'eye-closed');
  assert.equal(eyes.icon('open'), 'eye-open');
});

test('pressing a group eye hides everything when all are shown, and shows everything otherwise', () => {
  const ids = ['a', 'b', 'c'];
  let e = eyes.toggleGroup(eyes.empty(), ids);
  assert.equal(eyes.groupState(e, ids), 'closed');
  e = eyes.toggleGroup(e, ids);
  assert.equal(eyes.groupState(e, ids), 'open');
  e = eyes.toggleGroup(eyes.toggle(eyes.empty(), 'a'), ids);          // half -> all shown
  assert.equal(eyes.groupState(e, ids), 'open');
});

test('eye changes never mutate the previous state', () => {
  const a = eyes.empty();
  const b = eyes.toggle(a, 'x');
  assert.deepEqual(a, { hidden: {} });
  assert.equal(eyes.isHidden(b, 'x'), true);
});

test('visible ids keep manifest order, skip hidden parts, and include the background only while shown', () => {
  const m = fixture().clone(fixture().make);
  let e = eyes.toggle(eyes.toggle(eyes.empty(), 'eye_l'), 'hand_r');
  const ids = eyes.visibleIds(m, e);
  assert.ok(!ids.includes('eye_l') && !ids.includes('hand_r'));
  assert.deepEqual(ids.filter((i) => i !== 'background'), m.parts.map((p) => p.id).filter((i) => i !== 'eye_l' && i !== 'hand_r'));
  assert.equal(ids[ids.length - 1], 'background');
  assert.ok(!eyes.visibleIds(m, eyes.toggle(e, 'background')).includes('background'));
  assert.equal(eyes.hiddenCount(m, e), 2);
});

test('hair sections inherit the Hair eye, and combine merges the eye back', () => {
  let e = eyes.toggle(eyes.empty(), 'hair');
  e = eyes.afterSubdivide(e, ['hair_01', 'hair_02']);
  assert.ok(!eyes.isHidden(e, 'hair') && eyes.isHidden(e, 'hair_01') && eyes.isHidden(e, 'hair_02'));
  e = eyes.toggle(e, 'hair_01');                                         // one section shown again
  e = eyes.afterCombine(e, ['hair_01', 'hair_02']);
  assert.ok(!eyes.isHidden(e, 'hair'));                                  // not all hidden -> Hair is shown
  const all = eyes.afterCombine(eyes.setMany(eyes.empty(), ['hair_01', 'hair_02'], true), ['hair_01', 'hair_02']);
  assert.ok(eyes.isHidden(all, 'hair') && !eyes.isHidden(all, 'hair_01'));
});

test('prune forgets parts that are gone', () => {
  const m = fixture().make;
  const e = eyes.prune(eyes.setMany(eyes.empty(), ['head', 'ghost', 'background'], true), m);
  assert.deepEqual(Object.keys(e.hidden).sort(), ['background', 'head']);
});

// ------------------------------------------------------------------ parts
test('parts are listed under their group in manifest order', () => {
  const m = fixture().make;
  const g = parts.groupsOf(m);
  assert.deepEqual(g.map((x) => x.name), m.groups.map((x) => x.name));
  assert.deepEqual(g.flatMap((x) => x.parts.map((p) => p.id)), m.parts.map((p) => p.id));
  assert.equal(parts.partsInGroup(m, 'Hair').length, 1);
  assert.equal(parts.partsInGroup(m, 'Nope').length, 0);
});

test('the Hair group shows the sections after Cut hair, and the single part again after Combine', () => {
  const f = fixture();
  const view = parts.viewManifest(f.make, f.sub);
  const hair = parts.groupsOf(view).find((g) => g.name === 'Hair');
  assert.ok(hair.parts.length >= 2 && hair.parts.every((p) => p.kind === 'subdivision'));
  assert.equal(view.parts.length, f.make.parts.length - 1 + f.sub.parts.length);
  assert.deepEqual(parts.groupsOf(view).map((g) => g.name), parts.groupsOf(f.make).map((g) => g.name));   // group order unchanged
  const failed = f.clone(f.sub); failed.hair.subdivision.status = 'failed';
  assert.equal(parts.viewManifest(f.make, failed), f.make);                // a failed split leaves the single Hair part
  assert.equal(parts.viewManifest(f.make, null), f.make);
});

// ------------------------------------------------------------------ settings
test('settings are checked when loaded, so a damaged file cannot break the panel', () => {
  const s = settings.sanitize({ panel: { width: -5, height: 'x' }, group: 7, hairCount: 999, hairSmoothing: 'abc', autosaveMinutes: -3,
    backgroundFill: 'weird', container: 'nope', fullScreen: 'yes', replaceHair: 1 });
  assert.equal(s.panel.width, 240); assert.equal(s.panel.height, settings.DEFAULTS.panel.height);
  assert.equal(s.group, 'Head'); assert.equal(s.hairCount, 24); assert.equal(s.hairSmoothing, settings.DEFAULTS.hairSmoothing);
  assert.equal(s.autosaveMinutes, 0); assert.equal(s.backgroundFill, 'auto'); assert.equal(s.container, 'precomp');
  assert.equal(s.fullScreen, false); assert.equal(s.replaceHair, false);
  assert.deepEqual(settings.sanitize(null), settings.DEFAULTS);
});

test('settings survive blocked or empty storage', () => {
  const blocked = { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); } };
  assert.deepEqual(settings.load(blocked), settings.DEFAULTS);
  assert.equal(settings.save(blocked, settings.DEFAULTS), false);
  assert.deepEqual(settings.load({ getItem: () => '{not json', setItem() {} }), settings.DEFAULTS);
  assert.equal(settings.DEFAULTS.autosaveMinutes, 5);                      // default: every 5 minutes
});

// ------------------------------------------------------------------ cache
test('the preview cache keeps the most recently used pictures', () => {
  const c = new PreviewCache(2);
  c.set('a', 1); c.set('b', 2);
  assert.equal(c.get('a'), 1);                                              // a is now the most recent
  c.set('c', 3);                                                            // pushes out b
  assert.equal(c.has('b'), false); assert.equal(c.get('a'), 1); assert.equal(c.get('c'), 3);
  assert.equal(c.size, 2); assert.equal(c.get('zzz'), undefined);
  assert.equal(c.hits, 3); assert.equal(c.misses, 1);
});

// ------------------------------------------------------------------ plain language
const JARGON = /\b(manifest|onnx|aegp|json|api|stack ?trace|exception|undefined|null|cuda|vulkan|inference|tensor|http|localhost|token|ncnn|uxp|cep|extendscript|precomp(?!s))\b/i;
function walk(o, out, p) {
  if (typeof o === 'string') out.push([p, o]);
  else if (typeof o === 'function') { out.push([p + '()', o(3, 'Hero')]); }
  else if (Array.isArray(o)) o.forEach((x, i) => walk(x, out, p + '[' + i + ']'));
  else if (o && typeof o === 'object') Object.keys(o).forEach((k) => walk(o[k], out, p ? p + '.' + k : k));
  return out;
}
test('every panel string is plain language: no internal terms, short sentences', () => {
  const all = walk(strings, [], '');
  assert.ok(all.length > 80);
  for (const [p, s] of all) {
    assert.ok(!JARGON.test(s), `jargon in ${p}: "${s}"`);
    assert.ok(s.length <= 120, `too long (${s.length}) in ${p}: "${s}"`);
    s.split(/(?<=[.!?])\s+/).forEach((sent) => assert.ok(sent.split(/\s+/).length <= 22, `long sentence in ${p}: "${sent}"`));
  }
  for (const [p, s] of walk(strings.buttons, [], 'buttons')) assert.ok(s.length <= 30, `button text too long: ${p}`);
});

test('the welcome has the four steps in order, and the buttons use the documented names', () => {
  assert.deepEqual(strings.steps.list, ['Pick a layer.', 'Press Make Layers.', 'Hide any parts you do not want with the eye buttons.', 'Press Add to Project.']);
  const b = strings.buttons;
  assert.deepEqual([b.makeLayers, b.addToProject, b.backgroundRemoval, b.upscale, b.applyInProject, b.restore, b.combine, b.saveProject],
    ['Make Layers', 'Add to Project', 'Background Removal', 'Upscale 2x', 'Apply in Project', 'Restore', 'Combine', 'Save Project']);
  assert.equal(b.cutHair, 'Cut hair into subdivisions');
});

// ------------------------------------------------------------------ errors
test('errors say what happened, what to do, and point at help; no stack traces', () => {
  const logs = [];
  const p = errors.present(new Error('TypeError: x is undefined\n    at foo (bar.js:1:2)'), (l, m) => logs.push(m));
  assert.equal(p.what, strings.fallbackError.user_message);
  assert.ok(p.todo && p.topic);
  assert.ok(!/at foo|TypeError|undefined/.test(p.what + p.todo));           // the raw error is not shown
  assert.ok(logs.some((m) => /TypeError/.test(m)));                         // it went to the log instead
  const f = errors.present({ code: 'X', user_message: 'It broke.', what_to_do: 'Try again.', help_topic: 'troubleshooting', detail: 'internal note' }, (l, m) => logs.push(m));
  assert.deepEqual([f.what, f.todo, f.topic], ['It broke.', 'Try again.', 'troubleshooting']);
  const w = errors.present(errors.friendlyError(errors.local('SERVICE_DOWN')));
  assert.equal(w.topic, 'service-wont-start');
  for (const l of Object.values(errors.LOCAL)) assert.ok(!JARGON.test(l.user_message + l.what_to_do), l.code);
});

const test = require('node:test');
const assert = require('node:assert/strict');
const H = require('./helpers.js');
const sel = require('../js/core/selectors.js');
const eyes = require('../js/core/eyes.js');
const settings = require('../js/core/settings.js');
const strings = require('../js/core/strings.js');

const HOST_WRITES = ['add_to_project', 'restore', 'apply_upscale', 'save_project'];   // calls that can change the project
const wrote = (host) => host.calls.filter((c) => HOST_WRITES.includes(c.name)).map((c) => c.name);

// ------------------------------------------------------------------ auto preview, empty state, live update
test('on open the panel shows the selected layer without pressing anything', async () => {
  const { host, s } = await H.ready();
  assert.equal(s().preview.status, 'ready');
  assert.equal(s().preview.layerName, 'Hero');
  assert.match(s().preview.url, /^file:\/\/\/tmp\/voxy\/preview-/);
  assert.equal(host.count('export_frame'), 1);
  assert.deepEqual(wrote(host), []);                                   // looking never changes the project
  assert.equal(host.count('session_begin'), 1);
});

test('with nothing selected the panel says so clearly', async () => {
  for (const [patch, key] of [[{ has_layer: false, layer_id: 0, selected_layers: 0 }, 'noLayer'], [{ has_comp: false }, 'noComp'],
                              [{ selected_layers: 2, has_layer: false }, 'manyLayers'], [{ has_project: false }, 'noProject']]) {
    const h = new H.FakeHost(); h.set(patch);
    const { s, host } = await H.ready({ host: h });
    assert.equal(s().preview.status, 'empty');
    assert.equal(s().preview.emptyKey, key);
    assert.ok(strings.empty[key].length > 10);                          // there is a message for it
    assert.equal(host.count('export_frame'), 0);
  }
});

test('when the selection changes the preview follows, and going back reuses the cached picture', async () => {
  const e = await H.ready();
  const first = e.s().preview.url;
  e.host.set({ layer_id: 12, layer_name: 'Other', signature: '5:12:0' });
  await e.clock.advance(800);                                          // the panel polls the selection
  assert.equal(e.s().preview.layerName, 'Other');
  assert.notEqual(e.s().preview.url, first);
  assert.equal(e.host.count('export_frame'), 2);
  e.host.set({ layer_id: 11, layer_name: 'Hero', signature: '5:11:0' });
  await e.clock.advance(800);
  assert.equal(e.s().preview.url, first);
  assert.equal(e.host.count('export_frame'), 2);                       // cache hit: nothing was processed again
  await e.clock.advance(5000);                                         // an unchanged selection is not reloaded either
  assert.equal(e.host.count('export_frame'), 2);
  assert.ok(e.c.cache.hits >= 1);
});

test('a slow preview for an old selection never overwrites the new one', async () => {
  const e = await H.ready();
  let release;
  e.host.delay.export_frame = () => new Promise((r) => { release = r; });
  e.host.set({ layer_id: 12, layer_name: 'Slow', signature: 'slow' });
  const tick = e.clock.advance(800);
  await new Promise((r) => setImmediate(r));
  e.host.delay.export_frame = null;
  e.host.set({ layer_id: 13, layer_name: 'Fast', signature: 'fast' });
  release();
  await tick; await e.clock.advance(800);
  assert.equal(e.s().preview.layerName, 'Fast');
});

test('a changed project clears the parts, the cache, and the temporary upscale files', async () => {
  const e = await H.withParts();
  assert.ok(e.s().make.view);
  e.host.set({ project_id: 2, signature: '9:1:0' });
  await e.clock.advance(800);
  assert.equal(e.s().make.view, null);
  assert.equal(e.service.cleaned, 1);
});

// ------------------------------------------------------------------ Make Layers: parts appear in the panel, not in the project
test('Make Layers fills the panel and does not touch the project', async () => {
  const e = await H.ready();
  await e.c.makeLayers();
  assert.equal(e.s().make.status, 'done');
  assert.ok(sel.groupOptions(e.s()).includes('Hair'));
  assert.ok(e.s().make.view.parts.length >= 15);
  assert.deepEqual(wrote(e.host), []);                                 // nothing was added to the project
  const run = e.service.last('make_layers');
  assert.match(run.body.image_path, /^\/tmp\/voxy\/source-/);
  assert.deepEqual(run.body.options, { background_removal: false, background_fill: 'auto' });
  assert.match(e.s().banner.text, /parts are ready/);
});

test('Make Layers uses the saved toggles for the next job', async () => {
  const e = await H.ready();
  e.c.setSetting('backgroundRemoval', true); e.c.setSetting('backgroundFill', 'patch');
  await e.c.makeLayers();
  assert.deepEqual(e.service.last('make_layers').body.options, { background_removal: true, background_fill: 'patch' });
});

test('a failed Make Layers shows a friendly error and no parts', async () => {
  const e = await H.ready();
  const f = H.fixture().clone(H.fixture().make);
  f.job.status = 'failed'; f.parts = []; f.groups = []; f.skipped = []; f.background = null;
  f.errors = [{ code: 'GENERAL_MODEL_FAILED', user_message: 'Voxy could not cut your character into parts.', what_to_do: 'Try a picture with one character.', help_topic: 'make-layers-failed' }];
  e.service.results.make_layers = f;
  await e.c.makeLayers();
  assert.equal(e.s().make.view, null);
  assert.equal(e.s().banner.kind, 'error');
  assert.equal(e.s().banner.topic, 'make-layers-failed');
  assert.ok(e.s().banner.todo.length > 0);
  assert.equal(sel.canAdd(e.s()), false);
});

test('empty parts are reported as left out, never listed', async () => {
  const e = await H.ready();
  const f = H.fixture().clone(H.fixture().make);
  f.parts = f.parts.filter((p) => p.id !== 'foot_l');                    // a part with no pixels is not in the parts list ...
  f.groups.forEach((g) => { g.parts = g.parts.filter((i) => i !== 'foot_l'); });
  f.skipped = [{ id: 'foot_l', name: 'Foot L', group: 'Legs', reason: 'no_pixels' }];   // ... it is reported as left out
  e.service.results.make_layers = f;
  await e.c.makeLayers();
  assert.match(e.s().banner.text, /1 part had nothing in it/);
  assert.ok(!e.s().make.view.parts.some((p) => p.id === 'foot_l'));
});

test('only one job runs at a time', async () => {
  const e = await H.ready();
  let release;
  e.service.runDelay = () => new Promise((r) => { release = r; });
  const first = e.c.makeLayers();
  await new Promise((r) => setImmediate(r));
  assert.equal(e.s().busy, 'make');
  assert.equal(sel.canMakeLayers(e.s()), false);
  await e.c.makeLayers();                                              // ignored
  assert.equal(e.service.count('make_layers'), 1);
  release(); await first;
  assert.equal(e.s().busy, null);
});

// ------------------------------------------------------------------ eye buttons: panel only
test('eye buttons change only the panel: no command reaches After Effects', async () => {
  const e = await H.withParts();
  const before = e.host.calls.length;
  e.c.toggleEye('eye_l');
  e.c.toggleGroupEye('Arms');
  e.c.toggleGroupEye('Arms');
  e.c.toggleGroupEye('Hair');
  assert.equal(e.host.calls.length, before);
  assert.equal(sel.partEye(e.s(), 'eye_l'), 'closed');
  assert.equal(sel.groupEye(e.s(), 'Hair'), 'closed');
});

test('a group eye is half-open when only some parts are hidden', async () => {
  const e = await H.withParts();
  const face = e.s().make.view.groups.find((g) => g.name === 'Face').parts;
  e.c.toggleEye(face[0]);
  assert.equal(sel.groupEye(e.s(), 'Face'), 'half');
  face.forEach((id) => { if (!eyes.isHidden(e.s().make.eyes, id)) e.c.toggleEye(id); });
  assert.equal(sel.groupEye(e.s(), 'Face'), 'closed');
  e.c.toggleGroupEye('Face');
  assert.equal(sel.groupEye(e.s(), 'Face'), 'open');
});

test('eye states belong to this result and are cleared when Make Layers runs again', async () => {
  const e = await H.withParts();
  e.c.toggleEye('head'); e.c.toggleGroupEye('Legs');
  assert.ok(Object.keys(e.s().make.eyes.hidden).length > 2);
  await e.c.makeLayers();
  assert.deepEqual(e.s().make.eyes.hidden, {});
});

// ------------------------------------------------------------------ Add to Project
test('Add to Project sends exactly the visible parts and skips hidden ones', async () => {
  const e = await H.withParts();
  e.c.toggleEye('eye_l'); e.c.toggleGroupEye('Legs'); e.c.toggleEye('background');
  await e.c.addToProject();
  assert.equal(e.host.count('add_to_project'), 1);                      // one request = one undo step on the host
  const req = e.host.last('add_to_project').args;
  const all = e.s().make.view.parts.map((p) => p.id);
  const hiddenIds = ['eye_l', 'leg_l', 'leg_r', 'foot_l', 'foot_r'];
  assert.deepEqual(req.visible_ids, all.filter((i) => !hiddenIds.includes(i)));
  assert.ok(!req.visible_ids.includes('background'));
  assert.equal(req.options.container, 'precomp');
  assert.match(e.s().banner.text, /parts were added/);
});

test('Add to Project gives the host absolute file paths and does not need the panel to know the job folder', async () => {
  const e = await H.withParts();
  await e.c.addToProject();
  const m = e.host.last('add_to_project').args.manifest;
  const base = H.fixture().make.job.output_dir;
  assert.ok(m.parts.every((p) => p.image.startsWith(base + '/parts/')));
  assert.ok(m.background.image.startsWith(base + '/'));
  assert.equal(e.host.last('add_to_project').args.output_dir, '');
});

test('with every eye closed there is nothing to add, and the project is not touched', async () => {
  const e = await H.withParts();
  e.s().make.view.groups.forEach((g) => e.c.toggleGroupEye(g.name)); e.c.toggleEye('background');
  assert.equal(sel.canAdd(e.s()), false);
  await e.c.addToProject();
  assert.equal(e.host.count('add_to_project'), 0);
  assert.equal(e.s().banner.kind, 'error');
  assert.equal(e.s().banner.topic, 'eye-buttons');
});

test('the group container choice is saved and sent', async () => {
  const e = await H.withParts();
  e.c.setSetting('container', 'folder');
  await e.c.addToProject();
  assert.equal(e.host.last('add_to_project').args.options.container, 'folder');
});

test('a host error while adding is shown in plain words with a help topic', async () => {
  const e = await H.withParts();
  e.host.errors.add_to_project = { friendly: { code: 'ADD_FAILED', user_message: 'After Effects could not add the parts.', what_to_do: 'Nothing was changed. Try again.', help_topic: 'part-missing' } };
  await e.c.addToProject();
  assert.equal(e.s().banner.kind, 'error');
  assert.equal(e.s().banner.topic, 'part-missing');
  assert.equal(e.s().busy, null);
});

test('Restore asks the host to undo the last Add and is disabled when nothing is left', async () => {
  const e = await H.withParts();
  await e.c.addToProject();
  assert.equal(sel.canRestore(e.s()), true);
  await e.c.restore();
  assert.equal(e.host.count('restore'), 1);
  assert.equal(sel.canRestore(e.s()), false);                           // the host said nothing more to restore
  assert.equal(e.s().banner.text, strings.status.restored);
});

// ------------------------------------------------------------------ hair subdivision
test('the hair options appear only when the Hair group is selected', async () => {
  const e = await H.withParts();
  for (const g of ['Head', 'Face', 'Torso', 'Arms', 'Legs']) { e.c.selectGroup(g); assert.equal(sel.showHairOptions(e.s()), false, g); }
  e.c.selectGroup('Hair');
  assert.equal(sel.showHairOptions(e.s()), true);
  assert.equal(sel.canCutHair(e.s()), true);
  assert.equal(sel.canCombine(e.s()), false);                           // Combine appears only when sections exist
});

test('Cut hair sends the count and smoothing, shows the sections in the panel, and adds nothing to the project', async () => {
  const e = await H.withParts();
  e.c.selectGroup('Hair'); e.c.setSetting('hairCount', 5); e.c.setSetting('hairSmoothing', 0.6);
  await e.c.cutHair();
  const run = e.service.last('subdivide_hair');
  assert.deepEqual(run.body, { parent_job_id: 'make1', options: { count: 5, smoothing: 0.6 } });
  assert.ok(sel.hairIsSubdivided(e.s()) && sel.canCombine(e.s()));
  const hair = sel.groups(e.s()).find((g) => g.name === 'Hair').parts;
  assert.ok(hair.length >= 2 && hair.every((p) => p.kind === 'subdivision'));
  assert.deepEqual(wrote(e.host), []);
  assert.equal(sel.canCutHair(e.s()), false);
});

test('Add to Project then adds the sections inside the Hair group, with the right folders for their files', async () => {
  const e = await H.withParts();
  e.c.selectGroup('Hair'); await e.c.cutHair();
  await e.c.addToProject();
  const req = e.host.last('add_to_project').args;
  const secs = req.manifest.parts.filter((p) => p.kind === 'subdivision');
  assert.ok(secs.length >= 2 && req.visible_ids.includes(secs[0].id) && !req.visible_ids.includes('hair'));
  assert.ok(secs.every((p) => p.image.startsWith(H.fixture().sub.job.output_dir + '/')));          // from the hair job
  assert.ok(req.manifest.parts.filter((p) => p.kind !== 'subdivision').every((p) => p.image.startsWith(H.fixture().make.job.output_dir + '/')));
});

test('replacing the single Hair layer is only requested when sections exist and the user chose it', async () => {
  const e = await H.withParts();
  e.c.setSetting('replaceHair', true);
  await e.c.addToProject();
  assert.equal(e.host.last('add_to_project').args.options.replace_hair, false);     // no sections: nothing to replace it with
  e.c.selectGroup('Hair'); await e.c.cutHair(); await e.c.addToProject();
  assert.equal(e.host.last('add_to_project').args.options.replace_hair, true);
  e.c.setSetting('replaceHair', false); await e.c.addToProject();
  assert.equal(e.host.last('add_to_project').args.options.replace_hair, false);
});

test('Combine puts the single Hair part back, in the panel', async () => {
  const e = await H.withParts();
  e.c.selectGroup('Hair'); await e.c.cutHair();
  await e.c.combineHair();
  assert.equal(e.service.count('combine_hair'), 1);
  assert.equal(sel.hairIsSubdivided(e.s()), false);
  const hair = sel.groups(e.s()).find((g) => g.name === 'Hair').parts;
  assert.deepEqual(hair.map((p) => [p.id, p.name]), [['hair', 'Hair']]);
  assert.equal(e.s().banner.text, strings.status.hairCombined);
  assert.deepEqual(wrote(e.host), []);
});

test('Combine still works in the panel if the service cannot be asked', async () => {
  const e = await H.withParts();
  e.c.selectGroup('Hair'); await e.c.cutHair();
  e.service.results.combine_hair = () => { throw new Error('down'); };
  await e.c.combineHair();
  assert.equal(sel.hairIsSubdivided(e.s()), false);
  assert.ok(e.log.some(([l]) => l === 'warn'));
});

test('if the hair model fails the single Hair part stays and the error is friendly', async () => {
  const e = await H.withParts();
  e.c.selectGroup('Hair');
  const f = H.fixture().clone(H.fixture().sub);
  f.job.status = 'partial'; f.parts = [H.fixture().make.parts.find((p) => p.id === 'hair')];
  f.hair.subdivision = { status: 'failed', count: 0, smoothing: 0.3, pivot: null, error: { code: 'HAIR_MODEL_FAILED', user_message: 'Voxy could not split the hair into sections. Your single Hair part is still there.', what_to_do: 'Try again with fewer sections.', help_topic: 'hair-failed' } };
  e.service.results.subdivide_hair = f;
  await e.c.cutHair();
  assert.equal(sel.hairIsSubdivided(e.s()), false);
  assert.deepEqual(sel.groups(e.s()).find((g) => g.name === 'Hair').parts.map((p) => p.id), ['hair']);
  assert.equal(e.s().banner.kind, 'error');
  assert.match(e.s().banner.text, /single Hair part is still there/);
});

test('hair sections start with the eye state of the Hair part', async () => {
  const e = await H.withParts();
  e.c.toggleEye('hair'); e.c.selectGroup('Hair'); await e.c.cutHair();
  assert.equal(sel.groupEye(e.s(), 'Hair'), 'closed');
  await e.c.addToProject();
  assert.ok(!e.host.last('add_to_project').args.visible_ids.some((i) => i.startsWith('hair')));
});

// ------------------------------------------------------------------ upscale: selected layer only, nothing changes until Apply
test('Upscale 2x works on the selected layer and changes nothing in the project', async () => {
  const e = await H.ready();
  await e.c.upscale();
  const exp = e.host.calls.filter((c) => c.name === 'export_frame').pop();
  assert.equal(exp.args.layer_id, 11);                                  // the selected layer, and only that
  assert.equal(e.service.count('upscale'), 1);
  const u = e.s().upscale;
  assert.equal(u.status, 'ready'); assert.equal(u.layerName, 'Hero');
  assert.ok(u.original.startsWith('file://') && u.result.startsWith('http://svc/up1/upscale/'));      // shown beside each other
  assert.deepEqual(wrote(e.host), []);
  assert.equal(sel.canApply(e.s()), true);
  assert.match(e.s().banner.text, /Nothing in your project has changed yet/);
});

test('Upscale needs a selected picture layer', async () => {
  const h = new H.FakeHost(); h.set({ source_kind: 'precomp' });
  const e = await H.ready({ host: h });
  assert.equal(sel.canUpscale(e.s()), false);
  await e.c.upscale();
  assert.equal(e.service.count('upscale'), 0);
  assert.equal(e.s().banner.topic, 'upscale');
  h.set({ has_layer: false, layer_id: 0, selected_layers: 0, signature: 'x' });
  await e.c.refreshSelection();
  await e.c.upscale();
  assert.equal(e.service.count('upscale'), 0);
});

test('Apply in Project replaces that layer\'s source, once, and cleans up', async () => {
  const e = await H.ready();
  await e.c.upscale();
  e.host.set({ layer_id: 99, layer_name: 'Different', signature: 'other' });      // the selection moved on meanwhile
  await e.c.refreshSelection();
  await e.c.applyUpscale();
  const a = e.host.last('apply_upscale').args;
  assert.deepEqual(a, { layer_id: 11, image_path: '/tmp/up1/upscale/hero_x2.png', factor: 2 });   // still the layer that was upscaled
  assert.equal(e.host.count('apply_upscale'), 1);
  assert.equal(e.s().upscale.status, 'idle');
  assert.deepEqual(e.service.discarded, ['up1']);                       // the temporary file is deleted after the project has its own copy
  assert.match(e.s().banner.text, /"Hero" is now twice as big/);
});

test('Discard throws the result away and leaves the project exactly as it was', async () => {
  const e = await H.ready();
  await e.c.upscale();
  await e.c.discardUpscale();
  assert.deepEqual(e.service.discarded, ['up1']);
  assert.equal(e.s().upscale.status, 'idle');
  assert.deepEqual(wrote(e.host), []);
  assert.equal(sel.canApply(e.s()), false);
  assert.equal(e.s().banner.text, strings.status.discarded);
});

test('a second upscale replaces the pending result', async () => {
  const e = await H.ready();
  await e.c.upscale(); await e.c.upscale();
  assert.equal(e.service.discarded.length, 1);
  assert.equal(e.s().upscale.status, 'ready');
});

test('a failed upscale leaves the project unchanged and says what to do', async () => {
  const e = await H.ready();
  e.service.results.upscale = { job: { status: 'failed', output_dir: '/x' }, parts: [], errors: [{ code: 'UPSCALER_OOM', user_message: 'The upscale ran out of graphics memory. Your project was not changed.', what_to_do: 'Try a smaller layer.', help_topic: 'slow-or-memory' }] };
  await e.c.upscale();
  assert.equal(e.s().upscale.status, 'idle');
  assert.equal(e.s().banner.topic, 'slow-or-memory');
  assert.deepEqual(wrote(e.host), []);
});

test('the low memory setting is sent with the next upscale', async () => {
  const e = await H.ready();
  e.c.setSetting('lowMemory', true);
  await e.c.upscale();
  assert.equal(e.service.last('upscale').body.options.low_memory, true);
});

// ------------------------------------------------------------------ save and autosave
test('Save Project saves without asking, then confirms with the time', async () => {
  const e = await H.ready();
  await e.c.saveProject();
  assert.equal(e.host.count('save_project'), 1);
  assert.deepEqual(e.s().saved, { time: '14:05' });
  assert.equal(e.s().banner.text, 'Saved at 14:05.');
});

test('cancelling the first Save As is not an error', async () => {
  const e = await H.ready();
  e.host.results.save_project = { saved: false, cancelled: true };
  await e.c.saveProject();
  assert.equal(e.s().banner, null);
});

test('autosave runs on the interval from the settings and says so when it has run', async () => {
  const e = await H.ready();
  assert.deepEqual(e.clock.active.filter((m) => m === 300000), [300000]);          // default: every 5 minutes
  e.host.results.autosave = { status: 'saved', backup: 'C:\\x\\a-autosave.aep', time: '2026-02-03T14:10:00', size: 5 };
  await e.clock.advance(5 * 60000);
  assert.equal(e.host.count('autosave'), 1);
  assert.equal(e.s().banner.text, 'Backup saved at 14:10.');
  assert.equal(e.s().autosave.lastText, 'Backup saved at 14:10.');
  await e.clock.advance(5 * 60000);
  assert.equal(e.host.count('autosave'), 2);
});

test('changing the autosave interval restarts the timer, and 0 turns it off', async () => {
  const e = await H.ready();
  e.c.setSetting('autosaveMinutes', 2);
  assert.ok(e.clock.active.includes(120000) && !e.clock.active.includes(300000));
  e.c.setSetting('autosaveMinutes', 0);
  assert.ok(!e.clock.active.includes(120000) && !e.clock.active.includes(300000));
  assert.equal(e.s().autosave.on, false);
  assert.equal(JSON.parse(e.storage.getItem(settings.KEY)).autosaveMinutes, 0);
});

test('a failed autosave tells the user their saved project is untouched, and an unsafe one turns itself off', async () => {
  const e = await H.ready();
  e.host.results.autosave = { status: 'failed', error: { code: 'AUTOSAVE_FAILED', user_message: 'The automatic backup did not work. Your last saved project is untouched.', what_to_do: 'Press Save Project to save now.', help_topic: 'project-did-not-save' } };
  await e.clock.advance(300000);
  assert.match(e.s().banner.text, /untouched/); assert.equal(e.s().autosave.on, true);
  e.host.results.autosave = { status: 'disabled', error: { code: 'AUTOSAVE_UNSAFE', user_message: 'Automatic backups were turned off.', what_to_do: 'Use File > Save As.', help_topic: 'project-did-not-save' } };
  await e.clock.advance(300000);
  assert.equal(e.s().autosave.on, false);
  const calls = e.host.count('autosave');
  await e.clock.advance(600000);
  assert.equal(e.host.count('autosave'), calls);                         // stopped
});

test('a silent autosave (nothing changed) shows no message', async () => {
  const e = await H.ready();
  e.c.clearBanner();
  await e.clock.advance(300000);
  assert.equal(e.s().banner, null);
});

// ------------------------------------------------------------------ crash recovery
test('after a crash the panel explains and offers the newest backup', async () => {
  const h = new H.FakeHost();
  h.recovery = { backup: 'C:\\p\\Voxy Autosave\\p-autosave-1.aep', project: 'C:\\p\\p.aep', time: '2026-02-03T13:55:00', explanation: 'After Effects closed unexpectedly last time. Voxy had saved a backup copy.' };
  const e = await H.ready({ host: h });
  assert.equal(e.s().recovery.backup, h.recovery.backup);
  assert.match(e.s().recovery.explanation, /closed unexpectedly/);
  await e.c.acceptRecovery();
  assert.deepEqual(h.last('recovery_open').args, { backup: h.recovery.backup });
  assert.equal(e.s().recovery, null);
});

test('the recovery offer can be dismissed', async () => {
  const h = new H.FakeHost(); h.recovery = { backup: 'b', explanation: 'x' };
  const e = await H.ready({ host: h });
  e.c.dismissRecovery();
  assert.equal(e.s().recovery, null); assert.equal(h.count('recovery_open'), 0);
});

// ------------------------------------------------------------------ remembered settings, full screen
test('size, full screen, group, toggles and autosave interval come back next time', async () => {
  const a = await H.ready();
  a.c.rememberSize(900, 700); a.c.toggleFullScreen(); a.c.selectGroup('Arms');
  a.c.setSetting('backgroundRemoval', true); a.c.setSetting('hairCount', 9); a.c.setSetting('autosaveMinutes', 15); a.c.setSetting('container', 'folder');
  const b = await H.ready({ storage: a.storage });                       // a new session with the same storage
  const s = b.s().settings;
  assert.deepEqual([s.panel, s.fullScreen, s.group, s.backgroundRemoval, s.hairCount, s.autosaveMinutes, s.container],
    [{ width: 900, height: 700 }, true, 'Arms', true, 9, 15, 'folder']);
  assert.deepEqual(b.fullscreen, [true]);                               // and the panel re-enters full screen
});

test('full screen can be left again', async () => {
  const e = await H.ready();
  e.c.toggleFullScreen(); e.c.toggleFullScreen();
  assert.deepEqual(e.fullscreen, [true, false]);
  assert.equal(e.s().settings.fullScreen, false);
});

test('settings are also copied to the service for the uninstaller (best effort)', async () => {
  const e = await H.ready();
  e.c.setSetting('hairCount', 8);
  await new Promise((r) => setImmediate(r));
  assert.equal(e.service.settings.hairCount, 8);
});

// ------------------------------------------------------------------ errors never show stack traces
test('an unexpected failure shows a friendly message and logs the details', async () => {
  const e = await H.ready();
  e.service.results.make_layers = () => { throw new TypeError("Cannot read properties of undefined (reading 'x')"); };
  await e.c.makeLayers();
  assert.equal(e.s().banner.kind, 'error');
  assert.ok(!/TypeError|properties of undefined/.test(JSON.stringify(e.s().banner)));
  assert.equal(e.s().banner.topic, 'troubleshooting');
  assert.ok(e.log.some(([l, m]) => l === 'error' && /TypeError/.test(m)));
  assert.equal(e.s().busy, null);
});

test('the help button opens the topic that matches the error, and the log button opens the log', async () => {
  const e = await H.ready();
  e.c.openHelp('upscale');
  assert.deepEqual(e.opened[0], ['url', 'help/index.html#upscale']);
  e.c.openLog();
  assert.deepEqual(e.opened[1], ['path', '/logs/voxy-service.log']);
});

test('if the helper program is not running the panel says so in plain words', async () => {
  const h = new H.FakeHost();
  const { friendlyError, local } = require('../js/core/errors.js');
  h.errors.ensure_service = friendlyError(local('SERVICE_DOWN'));
  const e = await H.ready({ host: h });
  assert.equal(e.s().banner.topic, 'service-wont-start');
  assert.equal(sel.canMakeLayers(e.s()), true);                          // the panel still works for looking around
});

// ------------------------------------------------------------------ first run
test('the first time, the panel shows the welcome and checks the setup quietly', async () => {
  const e = await H.ready();
  assert.equal(e.s().welcome, true);
  assert.equal(e.s().setup, null);                                      // everything fine: it does not nag
  e.c.closeWelcome();
  assert.equal(e.s().welcome, false);
  const again = await H.ready({ storage: e.storage });
  assert.equal(again.s().welcome, false);
});

test('the first-open check speaks up when something needs attention, in plain words', async () => {
  const s = new H.FakeService();
  s.preflightResult = { overall: 'warn', checks: [{ id: 'gpu', status: 'warn', title: 'Graphics card', message: 'Voxy did not find a supported NVIDIA graphics card. It will use the slower mode.', what_to_do: 'Update your NVIDIA driver to use the fast mode. The slow mode will still work.' }] };
  s.healthResult = { status: 'ok', device: { cuda_available: false }, log_path: '/l' };
  const e = await H.ready({ service: s });
  assert.equal(e.s().setup.overall, 'warn');
  assert.match(e.s().setup.checks.find((c) => c.id === 'gpu').what_to_do, /Update your NVIDIA driver/);
  assert.equal(e.s().cpu, true);                                        // and the slow-mode warning is shown
});

test('Test setup runs the checks and the sample and reports each result', async () => {
  const e = await H.ready();
  await e.c.testSetup();
  const ids = e.s().setup.checks.map((c) => c.id);
  assert.deepEqual(ids, ['host', 'gpu', 'sample']);
  assert.ok(e.s().setup.checks.every((c) => c.status === 'ok'));
  assert.equal(e.s().setup.overall, 'ok');
  assert.equal(e.s().banner.text, strings.status.testOk);
  assert.equal(e.service.last('make_layers').body.sample, true);
});

test('Test setup reports a failing sample with what to do', async () => {
  const e = await H.ready();
  const f = H.fixture().clone(H.fixture().make);
  f.job.status = 'failed'; f.parts = []; f.groups = []; f.skipped = []; f.background = null;
  f.errors = [{ code: 'GENERAL_MODEL_MISSING', user_message: 'The part-cutting files are not installed.', what_to_do: 'Run the installer again to repair Voxy EasyAnimator.', help_topic: 'missing-files' }];
  e.service.results.make_layers = f;
  await e.c.testSetup();
  const sample = e.s().setup.checks.find((c) => c.id === 'sample');
  assert.equal(sample.status, 'fail'); assert.match(sample.what_to_do, /installer again/);
  assert.equal(e.s().setup.overall, 'fail');
});

test('the sample character shows parts in the panel but cannot be added to the project', async () => {
  const e = await H.ready();
  await e.c.runSample();
  assert.ok(e.s().make.view && e.s().make.sample);
  assert.equal(e.service.last('make_layers').body.sample, true);
  assert.equal(sel.canAdd(e.s()), false);
  await e.c.addToProject();
  assert.equal(e.host.count('add_to_project'), 0);
  assert.equal(e.s().banner.topic, 'first-run');
});

test('the slow-mode warning appears when the service reports it for a job', async () => {
  const e = await H.ready();
  assert.equal(e.s().cpu, false);
  const f = H.fixture().clone(H.fixture().make);
  f.warnings = [{ code: 'GPU_NOT_AVAILABLE', user_message: 'Voxy is using the slow mode.' }];
  e.service.results.make_layers = f;
  await e.c.makeLayers();
  assert.equal(e.s().cpu, true);
});

test('closing the panel ends the session cleanly and stops the timers', async () => {
  const e = await H.ready();
  await e.c.shutdown();
  assert.equal(e.host.count('session_end'), 1);
  assert.deepEqual(e.clock.active, []);
});

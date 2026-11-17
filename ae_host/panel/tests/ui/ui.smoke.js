// Browser test of the real panel UI against the real service (stub models) and a mock bridge.
// usage: node ui.smoke.js <service port> <service token> <screenshot dir>
const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');
const { start } = require('./mock_host.js');

const [svcPort, svcToken, shots] = process.argv.slice(2);
const panelDir = path.resolve(__dirname, '..', '..');
const sample = path.resolve(__dirname, '..', '..', '..', '..', 'service', 'voxy_service', 'assets', 'sample_character.png');
const results = [];
const check = (name, ok, extra) => { results.push({ name, ok: !!ok, extra: ok ? undefined : extra }); if (!ok) console.error('FAIL', name, extra || ''); };

(async () => {
  const TOKEN = 'hosttok';
  const host = await start({ panelDir, service: { port: Number(svcPort), token: svcToken }, sample, token: TOKEN });
  const url = `http://127.0.0.1:${host.port}/index.html?host=${host.port}&token=${TOKEN}`;
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 420, height: 900 }, deviceScaleFactor: 1 });
  const page = await ctx.newPage();
  const consoleErrors = [];
  page.on('pageerror', (e) => consoleErrors.push(String(e)));
  page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text()); });
  const text = () => page.evaluate(() => document.getElementById('app').innerText);
  const calls = (n) => host.state.calls.filter((c) => c.cmd === n);

  await page.goto(url);
  // ---- auto preview on open, with no button pressed
  await page.waitForSelector('#preview-img', { timeout: 20000 });
  const w = await page.$eval('#preview-img', (i) => new Promise((r) => (i.complete ? r(i.naturalWidth) : (i.onload = () => r(i.naturalWidth)))));
  check('preview loads without pressing anything', w === 512, w);
  check('shows the selected layer name', /Hero/.test(await text()));
  check('welcome shown the first time', await page.$('#welcome') !== null);
  check('the panel changed nothing in the project on open', ['add_to_project', 'restore', 'save_project', 'apply_upscale'].every((c) => calls(c).length === 0));
  check('Make Layers is available, Add to Project is not', !(await page.$eval('#make', (b) => b.disabled)) && (await page.$eval('#add', (b) => b.disabled)));
  check('no horizontal scroll at 420px', await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
  await page.screenshot({ path: path.join(shots, '01-open.png'), fullPage: true });

  // ---- Make Layers
  await page.click('#make');
  await page.waitForSelector('#parts-card', { timeout: 30000 });
  check('parts appear in the panel after Make Layers', (await page.$$('#part-list .part')).length >= 1);
  check('nothing was added to the project yet', calls('add_to_project').length === 0);
  const groups = await page.$$eval('#group option', (o) => o.map((x) => x.textContent.replace(/ \(\d+\)/, '')));
  check('groups are in the dropdown', ['Head', 'Face', 'Hair', 'Torso', 'Arms', 'Legs'].every((g) => groups.includes(g)), groups);
  check('the parts preview canvas is drawn', await page.$('#parts-canvas') !== null);
  await page.waitForFunction(() => { const c = document.getElementById('parts-canvas'); if (!c) return false; const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; for (let i = 3; i < d.length; i += 4000) if (d[i] > 0) return true; return false; }, null, { timeout: 15000 });
  check('hair options are hidden for the Head group', await page.$('#hair-options') === null);
  await page.screenshot({ path: path.join(shots, '02-parts-head.png'), fullPage: true });

  // ---- eye buttons: panel only
  const before = host.state.calls.length;
  await page.selectOption('#group', 'Face');
  const first = await page.$('#part-list .part [data-part-eye]');
  await first.click();
  check('an eye closes a part (row dims)', await page.$eval('#part-list .part', (r) => r.classList.contains('hidden')));
  check('group eye shows half-open when only some parts are hidden', (await page.$eval('#group-eye', (b) => b.dataset.eye)) === 'half' && /Some parts are hidden/.test(await text()));
  await page.click('#group-eye');
  check('pressing a half-open group eye shows everything', (await page.$eval('#group-eye', (b) => b.dataset.eye)) === 'open');
  await page.click('#group-eye');
  check('pressing again hides the whole group (closed eye)', (await page.$eval('#group-eye', (b) => b.dataset.eye)) === 'closed');
  check('eye buttons sent nothing to After Effects', host.state.calls.length === before + 0 || host.state.calls.slice(before).every((c) => c.cmd === 'selection'));
  await page.screenshot({ path: path.join(shots, '03-eyes.png'), fullPage: true });

  // ---- Add to Project: only visible parts
  await page.selectOption('#group', 'Arms');
  await page.click('#add');
  await page.waitForFunction(() => /added to your project/.test(document.getElementById('app').innerText), null, { timeout: 15000 });
  const add = calls('add_to_project').pop();
  const faceIds = ['eye_l', 'eye_r', 'iris_l', 'iris_r', 'eyelash_l', 'eyelash_r', 'eyebrow_l', 'eyebrow_r', 'nose', 'mouth'];
  check('Add to Project skipped the hidden Face group', faceIds.every((i) => !add.args.visible_ids.includes(i)) && add.args.visible_ids.includes('head'), add.args.visible_ids);
  check('exactly one add request', calls('add_to_project').length === 1);
  check('Restore is offered', !(await page.$eval('#restore', (b) => b.disabled)));

  // ---- hair
  await page.selectOption('#group', 'Hair');
  check('hair options appear for the Hair group', await page.$('#hair-options') !== null && /Cut hair into subdivisions/.test(await text()));
  check('Combine is not shown before subdividing', !/Combine/.test(await text()));
  await page.fill('#h-count', '4');
  await page.click('#cut-hair');
  await page.waitForFunction(() => /hair is cut into/.test(document.getElementById('app').innerText), null, { timeout: 30000 });
  check('sections are listed under the Hair group', (await page.$$('#part-list .part')).length >= 2);
  check('Combine now appears', await page.$('#combine') !== null);
  await page.screenshot({ path: path.join(shots, '04-hair-sections.png'), fullPage: true });
  await page.click('#combine');
  await page.waitForFunction(() => /one part again/.test(document.getElementById('app').innerText), null, { timeout: 20000 });
  check('Combine returns to a single Hair part', (await page.$$('#part-list .part')).length === 1);
  await page.selectOption('#group', 'Head');
  check('hair options hide again for other groups', await page.$('#hair-options') === null);

  // ---- upscale: selected layer only; nothing changes until Apply
  await page.click('#upscale');
  await page.waitForSelector('#compare', { timeout: 30000 });
  check('upscale shows original and result side by side', (await page.$$('#compare img')).length === 2);
  const sizes = await page.evaluate(() => Promise.all([...document.querySelectorAll('#compare img')].map((i) => new Promise((r) => (i.complete && i.naturalWidth ? r(i.naturalWidth) : (i.onload = () => r(i.naturalWidth)))))));
  check('the upscaled picture is twice as large', sizes[1] === sizes[0] * 2, sizes);
  check('Apply is not applied yet', calls('apply_upscale').length === 0);
  await page.screenshot({ path: path.join(shots, '05-upscale.png'), fullPage: true });
  await page.click('#discard');
  await page.waitForFunction(() => !document.getElementById('compare'));
  check('Discard leaves the project untouched', calls('apply_upscale').length === 0);
  await page.click('#upscale'); await page.waitForSelector('#compare', { timeout: 30000 });
  await page.click('#apply');
  await page.waitForFunction(() => /twice as big/.test(document.getElementById('app').innerText), null, { timeout: 15000 });
  const ap = calls('apply_upscale').pop();
  check('Apply in Project sent the selected layer and the bigger picture', ap && ap.args.layer_id === 11 && /x2/.test(ap.args.image_path) && ap.args.factor === 2, ap);

  // ---- save and autosave
  await page.click('#save');
  await page.waitForSelector('#saved-at', { timeout: 10000 });
  check('Save shows the time it was saved', /Saved at 14:05/.test(await text()));
  await page.fill('#autosave', '2'); await page.dispatchEvent('#autosave', 'change');
  check('the autosave interval is remembered', JSON.parse(await page.evaluate(() => localStorage.getItem('voxy.settings.v1'))).autosaveMinutes === 2);

  // ---- friendly errors
  host.state.errors.restore = { code: 'NOTHING_TO_RESTORE', user_message: 'There is nothing to restore.', what_to_do: 'Restore only undoes the last Add to Project.', help_topic: 'restore' };
  await page.click('#restore');
  await page.waitForSelector('.banner.error');
  const err = await text();
  check('errors say what happened and what to do', /There is nothing to restore/.test(err) && /only undoes the last Add/.test(err));
  check('errors have a help button and an open-log button', await page.$('#b-help') !== null && await page.$('#b-log') !== null);
  await page.click('#b-help');
  check('the help button opens the matching topic', (await page.evaluate(() => window.__opened || [])).some((u) => /#restore$/.test(u)));
  check('no stack trace words in the panel', !/(TypeError|Exception|undefined|\bat \w+\.js)/.test(await text()));

  // ---- plain language everywhere
  const all = await text();
  check('no internal terms on screen', !/\b(manifest|ONNX|AEGP|JSON|CUDA|Vulkan)\b/i.test(all), all.match(/\b(manifest|ONNX|AEGP|JSON|CUDA|Vulkan)\b/i));

  // ---- full screen is remembered
  await page.click('#fullscreen');
  check('full screen is switched on', await page.evaluate(() => document.body.classList.contains('full')));
  await page.setViewportSize({ width: 1400, height: 900 });
  await page.screenshot({ path: path.join(shots, '06-fullscreen.png'), fullPage: false });
  await page.reload({ waitUntil: 'load' });
  await page.waitForSelector('#parts-card', { timeout: 20000 });
  check('full screen comes back after reopening, with the work', await page.evaluate(() => document.body.classList.contains('full')) && await page.$('#parts-card') !== null);
  check('the button now offers normal size', /Normal size/.test(await text()));
  await page.click('#fullscreen');
  check('and it can be left again', !(await page.evaluate(() => document.body.classList.contains('full'))));

  check('no errors in the browser console', consoleErrors.length === 0, consoleErrors);
  await browser.close();
  host.server.close();
  const failed = results.filter((r) => !r.ok);
  console.log(JSON.stringify({ passed: results.length - failed.length, failed: failed.length, failures: failed }));
  process.exit(failed.length ? 1 : 0);
})().catch((e) => { console.error('UI test crashed:', e); process.exit(2); });

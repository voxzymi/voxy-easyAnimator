// Browser smoke test for the labeling tool UI (headless Chromium via Playwright).
// Usage: node label_tool.smoke.js <base url> <labels out dir>
const { chromium } = require('playwright');
const fs = require('fs'), path = require('path');
(async () => {
  const [base, out] = process.argv.slice(2);
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
  const errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('dialog', d => d.accept());
  await page.goto(base);
  await page.waitForSelector('#list .cls');
  const classes = await page.$$eval('#list .cls', els => els.map(e => e.textContent));
  const groupShown = classes.some(t => t.includes('hair') && t.includes('Hair'));
  // pick the hair class, paint a stroke across the canvas
  await page.click('#list .cls:has-text("hair")');
  const box = await page.locator('#pts').boundingBox();
  await page.mouse.move(box.x + box.width * 0.3, box.y + box.height * 0.2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.6, box.y + box.height * 0.2, { steps: 8 });
  await page.mouse.up();
  // place two key points with the Points tool
  await page.click('#tPoint');
  await page.mouse.click(box.x + box.width * 0.4, box.y + box.height * 0.5);
  await page.mouse.click(box.x + box.width * 0.6, box.y + box.height * 0.5);
  await page.click('#save');
  await page.waitForFunction(() => document.getElementById('status').textContent.startsWith('Saved'));
  // hair mode shows the separate set
  await page.click('#mHair');
  await page.waitForSelector('#list .cls');
  const hairClasses = await page.$$eval('#list .cls', els => els.length);
  const modeHelp = await page.textContent('#modeHelp');
  await browser.close();
  const dir = path.join(out, 'general', 'a');
  const res = {
    classesRendered: classes.length, groupShown, hairSections: hairClasses, separateSetHelp: modeHelp.includes('separate'),
    labelsSaved: fs.existsSync(path.join(dir, 'labels.png')),
    keypoints: JSON.parse(fs.readFileSync(path.join(dir, 'keypoints.json'))).map(p => p.name),
    hairDirCreatedByGeneralSave: fs.existsSync(path.join(out, 'hair')), errors,
  };
  console.log(JSON.stringify(res));
  process.exit(errors.length || !res.labelsSaved || res.keypoints.length !== 2 ? 1 : 0);
})();

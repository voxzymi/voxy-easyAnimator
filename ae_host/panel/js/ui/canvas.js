// Draws the parts that are switched on (the eye buttons) over each other, in stacking order. Panel only.
(function (root) {
  'use strict';
  const eyes = typeof require === 'function' ? require('../core/eyes.js') : root.Voxy.eyes;
  const images = new Map();

  function load(url, onload) {
    let im = images.get(url);
    if (!im) { im = new Image(); im.crossOrigin = 'anonymous'; im.onload = onload; im.src = url; images.set(url, im); }
    else if (!im.complete) im.addEventListener('load', onload, { once: true });
    return im;
  }

  // urlOf(part) gives the picture URL of a part; bgUrl is the redrawn background's URL.
  function draw(canvas, make, urlOf, bgUrl) {
    if (!canvas || !make.base || !make.base.source) return;
    const W = make.base.source.width, H = make.base.source.height;
    if (canvas.width !== W) canvas.width = W;
    if (canvas.height !== H) canvas.height = H;
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, W, H);
    const again = () => draw(canvas, make, urlOf, bgUrl);
    if (make.view.background && bgUrl && !eyes.isHidden(make.eyes, eyes.BACKGROUND_ID)) {
      const im = load(bgUrl, again);
      if (im.complete && im.naturalWidth) ctx.drawImage(im, 0, 0, W, H);
    }
    const shown = make.view.parts.filter((p) => !eyes.isHidden(make.eyes, p.id)).sort((a, b) => a.z_order - b.z_order);
    for (const p of shown) {
      const im = load(urlOf(p), again);
      if (im.complete && im.naturalWidth) ctx.drawImage(im, p.bbox[0], p.bbox[1], p.bbox[2], p.bbox[3]);
    }
    if (make.sectionOutlines !== false) {                                     // faint outlines help to see hair sections
      ctx.lineWidth = 1.5;
      for (const p of shown) {
        if (p.kind !== 'subdivision') continue;
        ctx.strokeStyle = 'hsla(' + ((parseInt(p.id.slice(-2), 10) || 0) * 47) % 360 + ',90%,60%,0.9)';
        for (const o of p.outline) { ctx.beginPath(); o.points.forEach((q, i) => (i ? ctx.lineTo(q[0], q[1]) : ctx.moveTo(q[0], q[1]))); ctx.closePath(); ctx.stroke(); }
      }
    }
  }
  const api = { draw };
  if (typeof module === 'object' && module.exports) module.exports = api; else (root.Voxy = root.Voxy || {}).canvas = api;
})(typeof self !== 'undefined' ? self : this);

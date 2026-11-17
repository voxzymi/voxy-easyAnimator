// Draws the panel from the controller's state. No logic here: every button calls a controller method.
(function (root) {
  'use strict';
  const need = (name, path) => (typeof require === 'function' ? require(path) : root.Voxy[name]);
  const strings = need('strings', '../core/strings.js');
  const sel = need('selectors', '../core/selectors.js');
  const eyes = need('eyes', '../core/eyes.js');
  const B = strings.buttons, L = strings.labels;

  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const EYE = {
    open: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>',
    half: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><path d="M12 5v14" /><path d="M12 9a3 3 0 0 1 0 6z" fill="currentColor"/></svg>',
    closed: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 3l18 18"/><path d="M10.6 5.1A10.6 10.6 0 0 1 12 5c6.4 0 10 7 10 7a17 17 0 0 1-3.2 4.2M6.2 6.6A17 17 0 0 0 2 12s3.6 7 10 7a10 10 0 0 0 4-.8"/></svg>',
  };
  const eyeBtn = (state, label, attrs) =>
    `<button class="eye quiet ${attrs.cls || ''}" ${attrs.data} aria-label="${esc(label)}" title="${esc(label)}" data-eye="${state}">${EYE[state]}</button>`;

  function btn(id, text, o) {
    const x = o || {};
    return `<button id="${id}" class="${x.primary ? 'primary' : x.quiet ? 'quiet' : ''}" ${x.disabled ? 'disabled' : ''} ${x.title ? `title="${esc(x.title)}"` : ''}>${esc(text)}</button>`;
  }

  function banner(s) {
    const b = s.banner;
    if (!b) return '';
    let h = `<div class="banner ${b.kind}" role="${b.kind === 'error' ? 'alert' : 'status'}"><div>${esc(b.text)}</div>`;
    if (b.kind === 'error') {
      h += `<div class="todo">${esc(b.todo)}</div><div class="actions">${btn('b-help', B.openHelp)}${btn('b-log', B.openLog, { quiet: true })}${btn('b-ok', B.tryAgain, { quiet: true })}</div>`;
      h = h.replace('b-help', 'b-help" data-topic="' + esc(b.topic));
    }
    return h + '</div>';
  }

  function previewCard(s) {
    const p = s.preview;
    let body;
    if (p.status === 'empty') body = `<div class="note">${esc(strings.empty[p.emptyKey] || strings.empty.noLayer)}</div>`;
    else if (p.status === 'loading') body = `<div class="note busy"><span class="spin"></span>${esc(strings.status.working)}</div>`;
    else if (p.status === 'error') body = `<div class="note">${esc(p.error.what)}<br><span class="small">${esc(p.error.todo)}</span></div>`;
    else body = `<img id="preview-img" src="${esc(p.url)}" alt="${esc(p.layerName)}">`;
    if (s.make.view && p.status !== 'empty') body = '<canvas id="parts-canvas" aria-label="Preview of the parts"></canvas>';
    const name = p.layerName ? `<span class="small">${esc(L.selectedLayer)}: <b>${esc(p.layerName)}</b></span>` : '<span></span>';
    return `<section class="card" id="preview-card"><div class="row spread"><h2>${esc(L.preview)}</h2>${name}</div>
      <div class="stage" id="stage">${body}</div>
      <div class="row" style="margin-top:6px">${btn('refresh', B.refresh, { quiet: true, disabled: !!s.busy })}
        ${s.make.view ? '<span class="small">' + esc(L.onlyPanel) + '</span>' : ''}</div></section>`;
  }

  function actionsCard(s) {
    const t = s.settings;
    const busy = s.busy ? `<div class="busy"><span class="spin"></span><span>${esc(s.step || strings.status.working)}</span>${btn('cancel', 'Cancel', { quiet: true })}</div>` : '';
    return `<section class="card" id="actions"><h2>Steps</h2>
      <div class="row">
        ${btn('make', B.makeLayers, { primary: true, disabled: !sel.canMakeLayers(s) })}
        ${btn('add', B.addToProject, { primary: true, disabled: !sel.canAdd(s), title: s.make.sample ? errTitle() : '' })}
        ${btn('restore', B.restore, { disabled: !sel.canRestore(s) })}
      </div>
      <div class="toggles" style="margin-top:8px">
        <label class="check"><input type="checkbox" id="t-bg" ${t.backgroundRemoval ? 'checked' : ''}> ${esc(B.backgroundRemoval)}</label>
        ${t.backgroundRemoval ? `<div class="field"><label for="t-fill">${esc(L.backgroundFill)}</label><select id="t-fill">
            ${['auto', 'model', 'patch'].map((v) => `<option value="${v}" ${t.backgroundFill === v ? 'selected' : ''}>${esc({ auto: L.fillAuto, model: L.fillModel, patch: L.fillPatch }[v])}</option>`).join('')}</select></div>` : ''}
        <div class="field"><label for="t-container">${esc(L.organize)}</label><select id="t-container">
          <option value="precomp" ${t.container === 'precomp' ? 'selected' : ''}>${esc(L.precomps)}</option>
          <option value="folder" ${t.container === 'folder' ? 'selected' : ''}>${esc(L.folders)}</option></select></div>
      </div>${busy}</section>`;
  }
  const errTitle = () => strings.errors && strings.errors.sample || 'The sample is only a preview.';

  function partsCard(s) {
    if (!s.make.view) return '';
    const groups = sel.groups(s), current = sel.currentGroup(s);
    const g = groups.find((x) => x.name === current) || groups[0];
    const gState = sel.groupEye(s, current);
    const groupLabel = { open: L.allVisible, half: L.someHidden, closed: L.allHidden }[gState];
    const rows = g.parts.map((p) => {
      const hidden = eyes.isHidden(s.make.eyes, p.id);
      return `<li class="part ${hidden ? 'hidden' : ''}" data-part="${esc(p.id)}">
        <img alt="" src="${esc(s.partUrl ? s.partUrl(p) : '')}"><span class="pname">${esc(p.name)}</span>
        ${eyeBtn(hidden ? 'closed' : 'open', hidden ? L.showPart : L.hidePart, { data: `data-part-eye="${esc(p.id)}"` })}</li>`;
    }).join('');
    let bg = '';
    if (s.make.view.background) {
      const hid = eyes.isHidden(s.make.eyes, eyes.BACKGROUND_ID);
      bg = `<ul class="parts" style="margin-top:8px"><li class="part ${hid ? 'hidden' : ''}" data-part="background"><span class="pname">${esc(L.redrawn)}</span>
        ${eyeBtn(hid ? 'closed' : 'open', hid ? L.showPart : L.hidePart, { data: 'data-part-eye="background"' })}</li></ul>`;
    }
    const hair = sel.showHairOptions(s) ? `<div class="card" id="hair-options" style="margin-top:8px;background:var(--raised)">
        ${btn('cut-hair', B.cutHair, { primary: true, disabled: !sel.canCutHair(s) })}
        ${sel.hairIsSubdivided(s) ? btn('combine', B.combine, { disabled: !sel.canCombine(s) }) : ''}
        <div class="field" style="margin-top:6px"><label for="h-count">${esc(L.hairCount)}</label>
          <input type="range" id="h-count" min="2" max="24" value="${t(s).hairCount}"><span id="h-count-v">${t(s).hairCount}</span></div>
        <div class="field"><label for="h-smooth">${esc(L.hairSmoothing)}</label>
          <input type="range" id="h-smooth" min="0" max="100" value="${Math.round(t(s).hairSmoothing * 100)}"><span id="h-smooth-v">${Math.round(t(s).hairSmoothing * 100)}%</span></div>
        <label class="check" style="margin-top:4px"><input type="checkbox" id="h-replace" ${t(s).replaceHair ? 'checked' : ''}> ${esc(L.replaceHair)}</label></div>` : '';
    return `<section class="card" id="parts-card"><div class="row spread"><h2>${esc(L.parts)}</h2><span class="small">${esc(L.eyeNote)}</span></div>
      <div class="groupbar"><label for="group">${esc(L.group)}</label>
        <select id="group">${groups.map((x) => `<option value="${esc(x.name)}" ${x.name === current ? 'selected' : ''}>${esc(x.name)} (${x.parts.length})</option>`).join('')}</select>
        ${eyeBtn(gState, gState === 'closed' ? L.showGroup : L.hideGroup, { cls: 'group', data: `id="group-eye" data-group-eye="${esc(current)}"` })}</div>
      <div class="small" id="group-state" style="margin:2px 0 0 0">${esc(groupLabel)}</div>
      <ul class="parts" id="part-list">${rows}</ul>${bg}${hair}</section>`;
  }
  const t = (s) => s.settings;

  function upscaleCard(s) {
    const u = s.upscale, x = s.selection;
    const name = x && x.has_layer ? `<span class="small">${esc(L.selectedLayer)}: <b>${esc(x.layer_name)}</b></span>` : `<span class="small">${esc(strings.empty.noLayer)}</span>`;
    const compare = u.status === 'ready' ? `<div class="compare" id="compare">
        <figure><div class="stage"><img src="${esc(u.original)}" alt="${esc(L.original)}"></div><figcaption>${esc(L.original)}</figcaption></figure>
        <figure><div class="stage"><img src="${esc(u.result)}" alt="${esc(L.upscaled)}"></div><figcaption>${esc(L.upscaled)}</figcaption></figure></div>
      <div class="small" style="margin:6px 0">${esc(u.layerName)}</div>
      <div class="row">${btn('apply', B.applyInProject, { primary: true, disabled: !sel.canApply(s) })}${btn('discard', B.discard, { disabled: !sel.canDiscard(s) })}</div>` : '';
    return `<section class="card" id="upscale-card"><div class="row spread"><h2>${esc(B.upscale)}</h2>${name}</div>
      <div class="row">${btn('upscale', B.upscale, { disabled: !sel.canUpscale(s) })}
        <label class="check"><input type="checkbox" id="u-low" ${t(s).lowMemory ? 'checked' : ''}> ${esc(L.lowMemory)}</label></div>${compare}</section>`;
  }

  function projectCard(s) {
    const m = t(s).autosaveMinutes;
    return `<section class="card" id="project-card"><h2>Project</h2>
      <div class="row">${btn('save', B.saveProject, { disabled: !sel.canSave(s) })}
        ${s.saved ? `<span class="small" id="saved-at">${esc(strings.status.savedAt(s.saved.time))}</span>` : ''}</div>
      <div class="field" style="margin-top:6px"><label for="autosave">${esc(L.autosave)}</label>
        <input type="number" id="autosave" min="0" max="120" value="${m}" style="width:64px"> <span class="small">${esc(m ? L.minutes : L.autosaveOff)}</span></div>
      ${s.autosave.lastText ? `<div class="small" id="autosave-msg">${esc(s.autosave.lastText)}</div>` : ''}</section>`;
  }

  function welcomeCard(s) {
    if (!s.welcome) return '';
    return `<section class="card" id="welcome"><h2>${esc(strings.steps.title)}</h2><div>${esc(strings.steps.intro)}</div>
      <ol class="steps">${strings.steps.list.map((x) => `<li>${esc(x)}</li>`).join('')}</ol>
      <div class="small">${esc(strings.steps.sample)}</div>
      <div class="row" style="margin-top:6px">${btn('sample', B.useSample, { primary: true, disabled: !!s.busy })}${btn('welcome-close', B.closeWelcome, { quiet: true })}</div></section>`;
  }

  function setupCard(s) {
    if (!s.setup) return '';
    const mark = { ok: '✓', warn: '!', fail: '×' };
    return `<section class="card" id="setup"><div class="row spread"><h2>${esc(B.testSetup)}</h2>${btn('setup-close', 'Close', { quiet: true })}</div>
      <ul class="checks">${s.setup.checks.map((c) => `<li><span class="mark ${c.status}" aria-label="${c.status}">${mark[c.status]}</span>
        <div><b>${esc(c.title)}</b><div>${esc(c.message)}</div>${c.what_to_do && c.status !== 'ok' ? `<div class="small">${esc(c.what_to_do)}</div>` : ''}</div></li>`).join('')}</ul></section>`;
  }

  function recoveryCard(s) {
    if (!s.recovery) return '';
    return `<section class="card banner warn" id="recovery"><div><b>${esc(strings.status.recovery)}</b></div><div class="small" style="margin:4px 0">${esc(s.recovery.explanation)}</div>
      <div class="row">${btn('rec-open', B.openBackup, { primary: true })}${btn('rec-no', B.noThanks, { quiet: true })}</div></section>`;
  }

  function render(s) {
    const header = `<div class="row spread span-all"><h1>${esc(strings.appName)}</h1><div class="row">
        ${btn('setup-btn', B.testSetup, { quiet: true, disabled: !!s.busy })}${btn('fullscreen', t(s).fullScreen ? B.normalSize : B.fullScreen, { quiet: true })}${btn('help', B.help, { quiet: true })}</div></div>`;
    const cpu = s.cpu ? `<div class="banner cpu span-all" id="cpu-warning" role="status">${esc(strings.status.cpuMode)}</div>` : '';
    // Narrow (docked) layout: one column with the buttons first. Full screen: the picture on the left, the controls on the right.
    const col = (inner) => `<div style="display:flex;flex-direction:column;gap:10px">${inner}</div>`;
    const body = t(s).fullScreen
      ? col(previewCard(s) + upscaleCard(s)) + col(welcomeCard(s) + actionsCard(s) + partsCard(s) + projectCard(s))
      : welcomeCard(s) + actionsCard(s) + previewCard(s) + partsCard(s) + upscaleCard(s) + projectCard(s);
    return `${header}${cpu}${recoveryCard(s)}${setupCard(s)}<div class="span-all">${banner(s)}</div>${body}`;
  }

  // ---- wiring: one delegated listener set, re-attached never (the container survives re-renders)
  function mount(container, controller, hooks) {
    const h = hooks || {};
    let last = '';
    function draw(state) {
      const view = Object.assign({}, state, { partUrl: h.partUrl ? (p) => h.partUrl(state, p) : null });
      const html = render(view);
      if (html !== last) {
        const active = document.activeElement && document.activeElement.id;
        const top = window.scrollY;
        container.innerHTML = html; last = html;
        if (active) { const el = document.getElementById(active); if (el) el.focus(); }
        window.scrollTo(0, top);
      }
      document.body.classList.toggle('full', !!state.settings.fullScreen);
      if (h.afterDraw) h.afterDraw(state);
    }
    container.addEventListener('click', (e) => {
      const t = e.target.closest('button');
      if (!t) return;
      const c = controller;
      const map = { make: () => c.makeLayers(), add: () => c.addToProject(), restore: () => c.restore(), refresh: () => c.refreshSelection({ force: true }),
        'cut-hair': () => c.cutHair(), combine: () => c.combineHair(), upscale: () => c.upscale(), apply: () => c.applyUpscale(), discard: () => c.discardUpscale(),
        save: () => c.saveProject(), sample: () => c.runSample(), 'welcome-close': () => c.closeWelcome(), 'setup-btn': () => c.testSetup(), 'setup-close': () => c.dismissSetup(),
        fullscreen: () => c.toggleFullScreen(), help: () => c.openHelp('first-run'), 'rec-open': () => c.acceptRecovery(), 'rec-no': () => c.dismissRecovery(),
        cancel: () => c.cancel(), 'b-ok': () => c.clearBanner(), 'b-log': () => c.openLog(), 'b-help': () => c.openHelp(t.getAttribute('data-topic')) };
      if (t.dataset.partEye) return c.toggleEye(t.dataset.partEye);
      if (t.dataset.groupEye) return c.toggleGroupEye(t.dataset.groupEye);
      if (map[t.id]) map[t.id]();
    });
    container.addEventListener('change', (e) => {
      const el = e.target, c = controller;
      const v = {
        group: () => c.selectGroup(el.value), 't-bg': () => c.setSetting('backgroundRemoval', el.checked), 't-fill': () => c.setSetting('backgroundFill', el.value),
        't-container': () => c.setSetting('container', el.value), 'h-replace': () => c.setSetting('replaceHair', el.checked), 'u-low': () => c.setSetting('lowMemory', el.checked),
        autosave: () => c.setSetting('autosaveMinutes', el.value),
      };
      if (v[el.id]) v[el.id]();
    });
    container.addEventListener('input', (e) => {
      const el = e.target, c = controller;
      if (el.id === 'h-count') { document.getElementById('h-count-v').textContent = el.value; c.setSetting('hairCount', el.value); }
      if (el.id === 'h-smooth') { document.getElementById('h-smooth-v').textContent = el.value + '%'; c.setSetting('hairSmoothing', Number(el.value) / 100); }
    });
    controller.subscribe(draw);
    draw(controller.state);
    return { draw };
  }

  const api = { render, mount, esc };
  if (typeof module === 'object' && module.exports) module.exports = api; else (root.Voxy = root.Voxy || {}).view = api;
})(typeof self !== 'undefined' ? self : this);

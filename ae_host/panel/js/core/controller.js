// The panel's brain. Holds the state, talks to After Effects (through the host bridge) and to the cutting service,
// and never touches the DOM, so every behaviour can be tested in Node with mocks.
(function (root) {
  'use strict';
  const need = (name, path) => (typeof require === 'function' ? require(path) : root.Voxy[name]);
  const strings = need('strings', './strings.js');
  const errors = need('errors', './errors.js');
  const eyes = need('eyes', './eyes.js');
  const parts = need('parts', './parts.js');
  const settingsLib = need('settings', './settings.js');
  const sel = need('selectors', './selectors.js');
  const PreviewCache = need('PreviewCache', './previewCache.js');

  const emptyMake = () => ({ status: 'idle', jobId: null, base: null, view: null, hairResult: null, hairJobId: null, sample: false, eyes: eyes.empty(), skipped: [], error: null });
  const emptyUpscale = () => ({ status: 'idle', layerId: null, layerName: '', original: '', result: '', resultPath: '', jobId: null });

  class Controller {
    constructor(deps) {
      this.host = deps.host;
      this.connectService = deps.connectService;
      this.storage = deps.storage;
      this.adapter = deps.adapter;
      this.timers = deps.timers;
      this.helpUrl = deps.helpUrl || ((t) => 'help/index.html#' + t);
      this.cache = new PreviewCache(12);
      this.service = null;
      this.listeners = [];
      this.previewSeq = 0;
      this.pollTimer = null;
      this.autosaveTimer = null;
      this.polling = false;
      this.state = {
        ready: false, settings: settingsLib.sanitize(null), selection: null,
        preview: { status: 'empty', url: '', layerName: '', emptyKey: 'noProject' },
        make: emptyMake(), upscale: emptyUpscale(), busy: null, step: '',
        banner: null, recovery: null, cpu: false, welcome: false, setup: null,
        autosave: { lastText: '', on: false }, canRestore: true, lastAdd: null, saved: null, logPath: '',
      };
    }

    // ---- plumbing
    subscribe(fn) { this.listeners.push(fn); return () => { this.listeners = this.listeners.filter((f) => f !== fn); }; }
    _set(patch) { Object.assign(this.state, patch); this.listeners.forEach((fn) => { try { fn(this.state); } catch (e) { this.adapter.log('error', String(e && e.stack || e)); } }); }
    _ok(text) { this._set({ banner: { kind: 'ok', text } }); }
    _info(text) { this._set({ banner: { kind: 'info', text } }); }
    _warn(text) { this._set({ banner: { kind: 'warn', text } }); }
    _err(e) {
      const p = errors.present(e, (lvl, m) => this.adapter.log(lvl, m));
      this._set({ banner: { kind: 'error', text: p.what, todo: p.todo, topic: p.topic, code: p.code } });
      return p;
    }
    clearBanner() { this._set({ banner: null }); }
    _saveSettings() {
      settingsLib.save(this.storage, this.state.settings);
      if (this.service) this.service.putSettings(this.state.settings).catch(() => {});   // a copy for the uninstaller; best effort
    }
    _time(iso) { const d = iso ? new Date(iso) : this.adapter.now(); return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0'); }
    _join(dir, rel) { return this.adapter.joinPath(dir, rel); }

    // ---- start up
    async init() {
      const st = settingsLib.load(this.storage);
      this._set({ settings: st, welcome: !st.welcomeDone });
      try {
        const r = await this.host.cmd('session_begin');
        if (r && r.recovery) this._set({ recovery: r.recovery });
      } catch (e) { this._err(e); }
      await this._connectService();
      if (this.state.settings.fullScreen) this.adapter.setFullScreen(true);
      await this.refreshSelection({ force: true });
      this.startPolling();
      this.startAutosave();
      this._set({ ready: true });
      if (!st.welcomeDone && this.service) await this.testSetup({ quiet: true });         // the first-open preflight
    }

    async _connectService() {
      this._set({ step: strings.status.starting });
      try {
        const info = await this.host.cmd('ensure_service');
        this.service = await this.connectService(info);
        const h = await this.service.health();
        this._set({ cpu: h.device && h.device.cuda_available === false, logPath: h.log_path || '' });
        const stored = await this.service.getSettings().catch(() => ({}));
        if (stored && Object.keys(stored).length && !settingsLib.load(this.storage).welcomeDone && !this.storage.getItem(settingsLib.KEY)) {
          this._set({ settings: settingsLib.sanitize(stored) });
        }
      } catch (e) { this.service = null; this._err(e); }
      this._set({ step: '' });
    }

    // ---- selection and preview (shown without pressing anything, kept up to date)
    startPolling() {
      this.stopPolling();
      this.pollTimer = this.timers.setInterval(() => {
        if (this.polling) return undefined;                                    // never overlap two polls
        this.polling = true;
        return this.refreshSelection().finally(() => { this.polling = false; });
      }, 700);
    }
    stopPolling() { if (this.pollTimer) { this.timers.clearInterval(this.pollTimer); this.pollTimer = null; } }

    async refreshSelection(opts) {
      const o = opts || {};
      let s;
      try { s = await this.host.cmd('selection'); } catch (e) { if (!this.state.selection) this._err(e); return; }
      const prev = this.state.selection;
      if (prev && prev.has_project && prev.project_id !== s.project_id) await this._projectChanged();
      this._set({ selection: s });
      const emptyKey = sel.emptyKey(this.state);
      if (emptyKey) { this.previewSeq++; this._set({ preview: { status: 'empty', url: '', layerName: '', emptyKey } }); return; }
      if (o.force) this.cache.delete(s.signature);
      if (!o.force && prev && prev.signature === s.signature && this.state.preview.status === 'ready') return;
      await this._loadPreview(s);
    }

    async _loadPreview(s) {
      const key = s.signature;
      const hit = this.cache.get(key);
      if (hit) { this._set({ preview: { status: 'ready', url: hit.url, layerName: s.layer_name, emptyKey: null } }); return; }   // no reprocessing
      const seq = ++this.previewSeq;
      this._set({ preview: { status: 'loading', url: '', layerName: s.layer_name, emptyKey: null } });
      try {
        const path = this.adapter.tempPath('preview-' + key.replace(/[^a-z0-9]+/gi, '_') + '.png');
        await this.host.cmd('export_frame', { path });
        if (seq !== this.previewSeq) return;                                  // the selection moved on while we worked
        const url = this.adapter.fileUrl(path);
        this.cache.set(key, { url, path });
        this._set({ preview: { status: 'ready', url, layerName: s.layer_name, emptyKey: null } });
      } catch (e) {
        if (seq !== this.previewSeq) return;
        const p = errors.present(e, (l, m) => this.adapter.log(l, m));
        this._set({ preview: { status: 'error', url: '', layerName: s.layer_name, emptyKey: null, error: p } });
      }
    }

    async _projectChanged() {
      this.cache.clear();
      if (this.service) this.service.cleanupTmp().catch(() => {});            // temporary upscale files go when a project closes
      this._set({ make: emptyMake(), upscale: emptyUpscale(), canRestore: true, lastAdd: null });
    }

    // ---- settings
    setSetting(key, value) {
      const next = settingsLib.sanitize(Object.assign({}, this.state.settings, { [key]: value }));
      this._set({ settings: next });
      this._saveSettings();
      if (key === 'autosaveMinutes') this.startAutosave();
      if (key === 'fullScreen') this.adapter.setFullScreen(next.fullScreen);
    }
    rememberSize(w, h) { this.setSetting('panel', { width: w, height: h }); }
    toggleFullScreen() { this.setSetting('fullScreen', !this.state.settings.fullScreen); }
    selectGroup(name) { this.setSetting('group', name); }
    closeWelcome() { this.setSetting('welcomeDone', true); this._set({ welcome: false }); }

    // ---- eyes (panel only: no host call, no project change)
    toggleEye(id) { this._set({ make: Object.assign({}, this.state.make, { eyes: eyes.toggle(this.state.make.eyes, id) }) }); }
    toggleGroupEye(name) {
      const ids = parts.idsInGroup(this.state.make.view, name);
      this._set({ make: Object.assign({}, this.state.make, { eyes: eyes.toggleGroup(this.state.make.eyes, ids) }) });
    }

    // ---- jobs
    _begin(kind, text) { if (this.state.busy) return false; this._set({ busy: kind, step: text || '', banner: text ? { kind: 'info', text } : null }); return true; }
    _end() { this._set({ busy: null, step: '' }); this.runningJob = null; }
    async cancel() { if (this.service && this.runningJob) await this.service.cancel(this.runningJob).catch(() => {}); }
    _runOpts() { return { onJob: (id) => { this.runningJob = id; }, onStep: () => {} }; }

    async makeLayers(opts) {
      const sample = !!(opts && opts.sample);
      if (!sample && !sel.canMakeLayers(this.state)) return;
      if (!this._begin('make', strings.status.makingLayers)) return;
      this._set({ make: emptyMake() });                                       // eye states are cleared on every run
      try {
        if (!this.service) await this._connectService();
        if (!this.service) return;
        const s = this.state.settings;
        const options = { background_removal: s.backgroundRemoval, background_fill: s.backgroundFill };
        let body;
        if (sample) body = { sample: true, options: { background_removal: false } };
        else {
          const path = this.adapter.tempPath('source-' + this.adapter.now().getTime() + '.png');
          await this.host.cmd('export_frame', { path });
          body = { image_path: path, options };
        }
        const { jobId, manifest } = await this.service.run('make_layers', body, this._runOpts());
        if (manifest.job.status === 'failed' || manifest.job.status === 'cancelled') {
          this._err(manifest.errors[0]);                                     // a failed Make Layers shows no parts
          return;
        }
        const names = parts.groupsOf(manifest).map((g) => g.name);
        if (!names.includes(s.group)) this.setSetting('group', names[0] || 'Head');
        const cpu = (manifest.warnings || []).some((w) => /^GPU_NOT_/.test(w.code));
        this._set({ make: Object.assign(emptyMake(), { status: 'done', jobId, base: manifest, view: manifest, sample, skipped: manifest.skipped || [] }), cpu: this.state.cpu || cpu });
        let text = strings.status.partsReady(manifest.parts.length);
        if (manifest.skipped && manifest.skipped.length) text += ' ' + strings.status.skipped(manifest.skipped.length);
        if (manifest.job.status === 'partial' && manifest.errors.length) this._warn(text + ' ' + manifest.errors[0].user_message); else this._ok(text);
      } catch (e) { this._err(e); } finally { this._end(); }
    }

    _requestManifest() {
      const make = this.state.make;
      const view = JSON.parse(JSON.stringify(make.view));
      const baseDir = make.base.job.output_dir;
      const hairDir = make.hairResult ? make.hairResult.job.output_dir : baseDir;
      view.parts.forEach((p) => { p.image = this._join(p.produced_by.model === 'hair' ? hairDir : baseDir, p.image); });
      if (view.background) view.background.image = this._join(baseDir, view.background.image);
      return view;
    }

    async addToProject() {
      const make = this.state.make;
      if (this.state.busy) return;
      if (make.sample) { this._err(errors.local('SAMPLE_ONLY')); return; }
      if (!make.view) { this._err(errors.local('NO_PARTS')); return; }
      const ids = eyes.visibleIds(make.view, make.eyes);
      if (!ids.length) { this._err(errors.local('NOTHING_TO_ADD')); return; }         // hidden parts are never added
      if (!this._begin('add', strings.status.adding)) return;
      try {
        const s = this.state.settings;
        const r = await this.host.cmd('add_to_project', {
          manifest: this._requestManifest(), output_dir: '', visible_ids: ids,
          options: { container: s.container, replace_hair: s.replaceHair && !!make.hairResult, include_background: true },
        });
        this._set({ canRestore: true, lastAdd: r });
        this._ok(strings.status.added(r.parts_added));
      } catch (e) { this._err(e); } finally { this._end(); }
    }

    async restore() {
      if (!sel.canRestore(this.state)) return;
      if (!this._begin('restore', '')) return;
      try {
        const r = await this.host.cmd('restore');
        this._set({ lastAdd: null, canRestore: !!(r && r.more) });          // the host says whether an earlier Add can still be undone
        this._ok(strings.status.restored);
      } catch (e) { this._err(e); } finally { this._end(); }
    }

    async cutHair() {
      if (!sel.canCutHair(this.state)) { if (!this.state.busy && this.state.settings.group !== 'Hair' && this.state.make.view) this._info(strings.status.hairOnlyHere); return; }
      if (!this._begin('hair', strings.status.hairWorking)) return;
      try {
        const s = this.state.settings;
        const { jobId, manifest } = await this.service.run('subdivide_hair', {
          parent_job_id: this.state.make.jobId, options: { count: s.hairCount, smoothing: s.hairSmoothing } }, this._runOpts());
        if (manifest.hair.subdivision.status !== 'ok') {                      // the single Hair part is still there
          const e = manifest.hair.subdivision.error || (manifest.errors && manifest.errors[0]);
          this._err(e);
          return;
        }
        const make = this.state.make;
        const view = parts.viewManifest(make.base, manifest);
        const subIds = manifest.parts.map((p) => p.id);
        this._set({ make: Object.assign({}, make, { hairResult: manifest, hairJobId: jobId, view, eyes: eyes.afterSubdivide(make.eyes, subIds) }) });
        this._ok(strings.status.hairReady(subIds.length));
      } catch (e) { this._err(e); } finally { this._end(); }
    }

    async combineHair() {
      if (!sel.canCombine(this.state)) return;
      if (!this._begin('hair', '')) return;
      const make = this.state.make;
      try {
        try { await this.service.run('combine_hair', { subdivision_job_id: make.hairJobId }, this._runOpts()); }
        catch (e) { this.adapter.log('warn', 'combine on the service failed; combining in the panel'); }    // the union is the original Hair part anyway
        const subIds = make.hairResult.parts.map((p) => p.id);
        this._set({ make: Object.assign({}, make, { hairResult: null, hairJobId: null, view: make.base, eyes: eyes.afterCombine(make.eyes, subIds) }) });
        this._ok(strings.status.hairCombined);
      } catch (e) { this._err(e); } finally { this._end(); }
    }

    // ---- upscale on the selected layer only; nothing changes in the project until Apply
    async upscale() {
      const s = this.state.selection;
      if (this.state.busy) return;
      if (!s || !s.has_layer) { this._err(errors.local('NO_LAYER')); return; }
      if (s.source_kind !== 'still') { this._err(errors.local('NOT_A_PICTURE_LAYER')); return; }
      if (!this._begin('upscale', strings.status.upscaling)) return;
      try {
        if (this.state.upscale.status === 'ready') await this._dropUpscale();
        const src = this.adapter.tempPath('upscale-in-' + this.adapter.now().getTime() + '.png');
        await this.host.cmd('export_frame', { path: src, layer_id: s.layer_id });
        const { jobId, manifest } = await this.service.run('upscale', { image_path: src, options: { low_memory: this.state.settings.lowMemory } }, this._runOpts());
        if (manifest.job.status !== 'ok') { this._err(manifest.errors[0]); return; }
        this._set({ upscale: { status: 'ready', layerId: s.layer_id, layerName: s.layer_name, original: this.adapter.fileUrl(src),
          result: this.service.fileUrl(jobId, manifest.upscale.image), resultPath: this._join(manifest.job.output_dir, manifest.upscale.image), jobId } });
        this._ok(strings.status.upscaleReady);
      } catch (e) { this._err(e); } finally { this._end(); }
    }
    async _dropUpscale() {
      const u = this.state.upscale;
      if (u.jobId && this.service) await this.service.discard(u.jobId).catch(() => {});
      this._set({ upscale: emptyUpscale() });
    }
    async discardUpscale() {
      if (!sel.canDiscard(this.state)) return;
      await this._dropUpscale();
      this._ok(strings.status.discarded);
    }
    async applyUpscale() {
      if (!sel.canApply(this.state)) return;
      if (!this._begin('apply', '')) return;
      const u = this.state.upscale;
      try {
        await this.host.cmd('apply_upscale', { layer_id: u.layerId, image_path: u.resultPath, factor: 2 });
        await this._dropUpscale();                                            // the project now has its own copy of the picture
        this.cache.clear();
        this._ok(strings.status.applied(u.layerName));
        await this.refreshSelection({ force: true });
      } catch (e) { this._err(e); } finally { this._end(); }
    }

    // ---- save, autosave, recovery
    async saveProject() {
      if (!sel.canSave(this.state)) return;
      if (!this._begin('save', '')) return;
      try {
        const r = await this.host.cmd('save_project');                         // no questions first
        if (r.saved) { this._set({ saved: { time: this._time(r.time) } }); this._ok(strings.status.savedAt(this._time(r.time))); }
        else this._clearOnly();
      } catch (e) { this._err(e); } finally { this._end(); }
    }
    _clearOnly() { this._set({ banner: null }); }

    startAutosave() {
      if (this.autosaveTimer) { this.timers.clearInterval(this.autosaveTimer); this.autosaveTimer = null; }
      const m = this.state.settings.autosaveMinutes;
      this._set({ autosave: Object.assign({}, this.state.autosave, { on: m > 0 }) });
      if (m > 0) this.autosaveTimer = this.timers.setInterval(() => { this.autosaveTick(); }, m * 60000);
    }
    stopAutosave() { if (this.autosaveTimer) { this.timers.clearInterval(this.autosaveTimer); this.autosaveTimer = null; } this._set({ autosave: Object.assign({}, this.state.autosave, { on: false }) }); }
    async autosaveTick() {
      if (this.state.busy === 'save') return;
      try {
        const r = await this.host.cmd('autosave');
        if (r.status === 'saved') { const text = strings.status.backupAt(this._time(r.time)); this._set({ autosave: Object.assign({}, this.state.autosave, { lastText: text }) }); this._ok(text); }
        else if (r.status === 'failed') this._err(r.error);
        else if (r.status === 'disabled') { this.stopAutosave(); this._err(r.error); }
      } catch (e) { this._err(e); }
    }
    async acceptRecovery() {
      const r = this.state.recovery;
      if (!r) return;
      try { await this.host.cmd('recovery_open', { backup: r.backup }); this._set({ recovery: null }); } catch (e) { this._err(e); }
    }
    dismissRecovery() { this._set({ recovery: null }); }

    // ---- first run, test setup, help
    async testSetup(opts) {
      const quiet = !!(opts && opts.quiet);
      if (!quiet && this.state.busy) return;
      if (!quiet) this._begin('test', strings.status.testing);
      try {
        const checks = [];
        try { await this.host.cmd('ping'); checks.push({ id: 'host', status: 'ok', title: 'After Effects', message: 'The panel is connected to After Effects.' }); }
        catch (e) { checks.push({ id: 'host', status: 'fail', title: 'After Effects', message: errors.present(e).what, what_to_do: errors.present(e).todo }); }
        if (!this.service) await this._connectService();
        if (this.service) {
          const pf = await this.service.preflight();
          pf.checks.forEach((c) => checks.push(c));
          if (!quiet) {
            try {
              const { manifest } = await this.service.run('make_layers', { sample: true, options: {} }, this._runOpts());
              checks.push(manifest.job.status === 'failed'
                ? { id: 'sample', status: 'fail', title: 'Sample character', message: manifest.errors[0].user_message, what_to_do: manifest.errors[0].what_to_do }
                : { id: 'sample', status: 'ok', title: 'Sample character', message: strings.status.sampleReady });
            } catch (e) { const p = errors.present(e); checks.push({ id: 'sample', status: 'fail', title: 'Sample character', message: p.what, what_to_do: p.todo }); }
          }
        } else checks.push({ id: 'service', status: 'fail', title: 'Helper program', message: errors.local('SERVICE_DOWN').user_message, what_to_do: errors.local('SERVICE_DOWN').what_to_do });
        const worst = checks.some((c) => c.status === 'fail') ? 'fail' : checks.some((c) => c.status === 'warn') ? 'warn' : 'ok';
        this._set({ setup: { overall: worst, checks, quiet } });
        if (!quiet) { if (worst === 'ok') this._ok(strings.status.testOk); else this._warn(strings.status.testNotOk); }
        else if (worst === 'ok') this._set({ setup: null });                 // the first-open check only speaks up when something is wrong
      } catch (e) { if (!quiet) this._err(e); } finally { if (!quiet) this._end(); }
    }
    dismissSetup() { this._set({ setup: null }); }
    async runSample() { await this.makeLayers({ sample: true }); }
    openHelp(topic) { this.adapter.openUrl(this.helpUrl(topic || 'first-run')); }
    openLog() { if (this.state.logPath) this.adapter.openPath(this.state.logPath); }

    async shutdown() {
      this.stopPolling(); this.stopAutosave();
      try { await this.host.cmd('session_end'); } catch (e) { /* closing anyway */ }
    }
  }

  if (typeof module === 'object' && module.exports) module.exports = Controller; else (root.Voxy = root.Voxy || {}).Controller = Controller;
})(typeof self !== 'undefined' ? self : this);

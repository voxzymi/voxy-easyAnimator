// Test doubles for the panel: a fake After Effects bridge, a fake cutting service, a manual clock and memory storage.
// The manifests are the real ones the Python service produces (ae_host/tests/make_fixture.py).
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');
const Controller = require('../js/core/controller.js');

let fx = null;
function fixture() {
  if (fx) return fx;
  let dir = process.env.VOXY_FIXTURE;
  if (!dir || !fs.existsSync(path.join(dir, 'make_layers', 'manifest.json'))) {
    dir = fs.mkdtempSync(path.join(os.tmpdir(), 'voxy-fixture-'));
    execFileSync(process.env.VOXY_PYTHON || 'python3', [path.join(__dirname, '..', '..', 'tests', 'make_fixture.py'), dir], { stdio: 'pipe' });
  }
  const read = (n) => JSON.parse(fs.readFileSync(path.join(dir, n, 'manifest.json'), 'utf8'));
  const make = read('make_layers'); make.job.output_dir = path.join(dir, 'make_layers');
  const sub = read('subdivide'); sub.job.output_dir = path.join(dir, 'subdivide');
  fx = { dir, make, sub, clone: (o) => JSON.parse(JSON.stringify(o)) };
  return fx;
}

class Clock {
  constructor() { this.t = 0; this.timers = []; this.next = 1; }
  setInterval(fn, ms) { const id = this.next++; this.timers.push({ id, fn, ms, due: this.t + ms }); return id; }
  clearInterval(id) { this.timers = this.timers.filter((x) => x.id !== id); }
  async advance(ms) {
    const end = this.t + ms;
    for (;;) {
      const due = this.timers.filter((x) => x.due <= end).sort((a, b) => a.due - b.due)[0];
      if (!due) break;
      this.t = due.due; due.due += due.ms; await due.fn();
    }
    this.t = end;
  }
  get active() { return this.timers.map((x) => x.ms); }
}

class FakeHost {
  constructor() {
    this.calls = [];
    this.selection = { has_project: true, project_id: 1, has_comp: true, comp_id: 5, comp_name: 'Main', width: 400, height: 640, fps: 24, duration: 5,
      time: 0, selected_layers: 1, has_layer: true, layer_id: 11, layer_name: 'Hero', source_kind: 'still', signature: '5:11:0' };
    this.results = {};
    this.errors = {};
    this.delay = {};
    this.recovery = null;
  }
  set(patch) { Object.assign(this.selection, patch); }
  count(name) { return this.calls.filter((c) => c.name === name).length; }
  last(name) { return this.calls.filter((c) => c.name === name).pop(); }
  async cmd(name, args) {
    this.calls.push({ name, args: args || {} });
    if (this.delay[name]) await this.delay[name]();
    if (this.errors[name]) throw this.errors[name];
    if (this.results[name]) return typeof this.results[name] === 'function' ? this.results[name](args) : this.results[name];
    switch (name) {
      case 'selection': return Object.assign({}, this.selection);
      case 'export_frame': return { path: args.path };
      case 'ensure_service': return { port: 4242, token: 'tok' };
      case 'session_begin': return { recovery: this.recovery };
      case 'add_to_project': return { parts_added: (args.visible_ids || []).filter((i) => i !== 'background').length };
      case 'restore': return { more: false };
      case 'save_project': return { saved: true, time: '2026-02-03T14:05:09', path: 'C:\\p.aep', name: 'p.aep' };
      case 'autosave': return { status: 'skipped' };
      default: return {};
    }
  }
}

class FakeService {
  constructor() {
    this.runs = []; this.discarded = []; this.cancelled = []; this.cleaned = 0; this.settings = {}; this.preflightResult = { overall: 'ok', checks: [{ id: 'gpu', status: 'ok', title: 'Graphics card', message: 'Your graphics card is supported.' }] };
    this.healthResult = { status: 'ok', device: { cuda_available: true }, log_path: '/logs/voxy-service.log' };
    this.results = {}; this.runDelay = null;
  }
  async job(id) {
    const f = fixture();
    if (id === 'make1') return { job_id: id, status: 'done', manifest: f.clone(f.make) };
    if (id === 'sub1') return { job_id: id, status: 'done', manifest: f.clone(f.sub) };
    const e = new Error('gone'); throw e;
  }
  async health() { return this.healthResult; }
  async preflight() { return this.preflightResult; }
  async getSettings() { return this.settings; }
  async putSettings(s) { this.settings = s; return { ok: true }; }
  async discard(id) { this.discarded.push(id); return { ok: true }; }
  async cancel(id) { this.cancelled.push(id); return { ok: true }; }
  async cleanupTmp() { this.cleaned++; return { removed: 0 }; }
  fileUrl(job, rel) { return 'http://svc/' + job + '/' + rel; }
  async run(type, body, opts) {
    this.runs.push({ type, body });
    if (opts && opts.onJob) opts.onJob('job' + this.runs.length);
    if (this.runDelay) await this.runDelay(type);
    const f = fixture();
    if (this.results[type]) { const r = typeof this.results[type] === 'function' ? this.results[type](body) : this.results[type]; return { jobId: 'job' + this.runs.length, manifest: r }; }
    if (type === 'make_layers') return { jobId: 'make1', manifest: f.clone(f.make) };
    if (type === 'subdivide_hair') return { jobId: 'sub1', manifest: f.clone(f.sub) };
    if (type === 'combine_hair') { const m = f.clone(f.make); m.job.type = 'combine_hair'; return { jobId: 'comb1', manifest: m }; }
    if (type === 'upscale') return { jobId: 'up1', manifest: { job: { id: 'up1', type: 'upscale', status: 'ok', output_dir: '/tmp/up1' }, errors: [],
      upscale: { status: 'ok', image: 'upscale/hero_x2.png', scale: 2, input_size: [400, 640], output_size: [800, 1280] }, parts: [] } };
    throw new Error('unexpected job ' + type);
  }
  count(t) { return this.runs.filter((r) => r.type === t).length; }
  last(t) { return this.runs.filter((r) => r.type === t).pop(); }
}

class MemoryStorage {
  constructor() { this.m = {}; }
  getItem(k) { return k in this.m ? this.m[k] : null; }
  setItem(k, v) { this.m[k] = String(v); }
}

function makeEnv(opts) {
  const o = opts || {};
  const host = o.host || new FakeHost();
  const service = o.service || new FakeService();
  const clock = o.clock || new Clock();
  const storage = o.storage || new MemoryStorage();
  const log = [], opened = [], fullscreen = [];
  const adapter = {
    tempPath: (n) => '/tmp/voxy/' + n, fileUrl: (p) => 'file://' + p, joinPath: (a, b) => a + '/' + b,
    openUrl: (u) => opened.push(['url', u]), openPath: (p) => opened.push(['path', p]),
    log: (lvl, msg) => log.push([lvl, msg]), setFullScreen: (v) => fullscreen.push(v), now: () => new Date(2026, 1, 3, 14, 5, 9),
  };
  const c = new Controller({ host, connectService: async () => service, storage, adapter, timers: clock, helpUrl: (t) => 'help/index.html#' + t });
  return { c, host, service, clock, storage, adapter, log, opened, fullscreen, s: () => c.state };
}
async function ready(opts) { const e = makeEnv(opts); await e.c.init(); return e; }
async function withParts(opts) { const e = await ready(opts); await e.c.makeLayers(); return e; }

module.exports = { fixture, Clock, FakeHost, FakeService, MemoryStorage, makeEnv, ready, withParts };

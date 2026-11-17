// HTTP clients for the two local helpers: the cutting service and the After Effects bridge.
// `fetchFn` is injected (window.fetch in the panel, a mock in tests).
(function (root) {
  'use strict';
  const errors = typeof require === 'function' ? require('../core/errors.js') : root.Voxy.errors;

  async function request(fetchFn, base, token, method, path, body, down) {
    let res;
    try {
      res = await fetchFn(base + path, {
        method, headers: Object.assign({ 'X-Voxy-Token': token }, body !== undefined ? { 'Content-Type': 'application/json' } : {}),
        body: body !== undefined ? JSON.stringify(body) : undefined,
      });
    } catch (e) {
      throw errors.friendlyError(errors.local(down));                 // could not connect at all
    }
    let data = null;
    try { data = await res.json(); } catch (e) { /* empty body */ }
    if (!res.ok || (data && data.ok === false)) {
      const err = data && data.error;
      if (errors.isFriendly(err)) throw errors.friendlyError(err);
      throw errors.friendlyError(errors.local(down));
    }
    return data;
  }

  class ServiceClient {
    constructor(fetchFn, info, sleep) {
      this.fetch = fetchFn; this.info = info; this.base = 'http://127.0.0.1:' + info.port; this.token = info.token;
      this.sleep = sleep || ((ms) => new Promise((r) => setTimeout(r, ms)));
    }
    _req(method, path, body) { return request(this.fetch, this.base, this.token, method, path, body, 'SERVICE_DOWN'); }
    health() { return this._req('GET', '/v1/health'); }
    preflight() { return this._req('GET', '/v1/preflight'); }
    getSettings() { return this._req('GET', '/v1/settings'); }
    putSettings(s) { return this._req('PUT', '/v1/settings', s); }
    submit(type, body) { return this._req('POST', '/v1/jobs', Object.assign({ type }, body || {})); }
    job(id, wait) { return this._req('GET', '/v1/jobs/' + id + (wait ? '?wait=1&timeout=' + wait : '')); }
    cancel(id) { return this._req('POST', '/v1/jobs/' + id + '/cancel'); }
    discard(id) { return this._req('POST', '/v1/jobs/' + id + '/discard'); }
    cleanupTmp() { return this._req('POST', '/v1/tmp/cleanup'); }
    // <img> tags cannot send headers, so picture URLs carry the token.
    fileUrl(jobId, rel) { return this.base + '/v1/jobs/' + jobId + '/files/' + rel.split('/').map(encodeURIComponent).join('/') + '?token=' + encodeURIComponent(this.token); }
    // Runs a job to the end. onStep gets the step name while it runs. Resolves with { jobId, manifest }.
    async run(type, body, opts) {
      const o = opts || {};
      const sub = await this.submit(type, body);
      if (o.onJob) o.onJob(sub.job_id);
      for (;;) {
        const j = await this.job(sub.job_id, 2);
        if (o.onStep && j.step) o.onStep(j.step);
        if (j.status === 'done' || j.status === 'cancelled') return { jobId: sub.job_id, manifest: j.manifest };
        await this.sleep(o.pollMs === undefined ? 50 : o.pollMs);
      }
    }
  }

  class HostClient {
    constructor(fetchFn, info) { this.fetch = fetchFn; this.info = info; this.base = 'http://127.0.0.1:' + info.port; this.token = info.token; }
    async cmd(name, args) {
      const data = await request(this.fetch, this.base, this.token, 'POST', '/v1/cmd', Object.assign({ cmd: name }, args || {}), 'HOST_DOWN');
      return data.result;
    }
  }

  const api = { ServiceClient, HostClient };
  if (typeof module === 'object' && module.exports) module.exports = api; else (root.Voxy = root.Voxy || {}).clients = api;
})(typeof self !== 'undefined' ? self : this);

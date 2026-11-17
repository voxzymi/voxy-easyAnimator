const test = require('node:test');
const assert = require('node:assert/strict');
const { ServiceClient, HostClient } = require('../js/api/clients.js');

function mockFetch(routes) {
  const calls = [];
  const f = async (url, opts) => {
    calls.push({ url, opts });
    const r = routes(url, opts, calls.length);
    if (r instanceof Error) throw r;
    return { ok: r.status < 400, status: r.status, json: async () => r.body };
  };
  f.calls = calls;
  return f;
}

test('the host client sends the token and returns the result', async () => {
  const f = mockFetch(() => ({ status: 200, body: { ok: true, result: { pong: true } } }));
  const r = await new HostClient(f, { port: 99, token: 'T' }).cmd('ping', { a: 1 });
  assert.deepEqual(r, { pong: true });
  assert.equal(f.calls[0].url, 'http://127.0.0.1:99/v1/cmd');
  assert.equal(f.calls[0].opts.headers['X-Voxy-Token'], 'T');
  assert.deepEqual(JSON.parse(f.calls[0].opts.body), { cmd: 'ping', a: 1 });
});

test('a host error keeps its friendly text', async () => {
  const err = { code: 'NO_LAYER', user_message: 'No layer is selected.', what_to_do: 'Click your character layer.', help_topic: 'no-layer' };
  const f = mockFetch(() => ({ status: 200, body: { ok: false, error: err } }));
  await assert.rejects(new HostClient(f, { port: 1, token: 't' }).cmd('export_frame'), (e) => e.friendly.code === 'NO_LAYER' && e.friendly.what_to_do.length > 0);
});

test('when After Effects or the service cannot be reached the message is friendly, not a network error', async () => {
  const down = mockFetch(() => new TypeError('Failed to fetch'));
  await assert.rejects(new HostClient(down, { port: 1, token: 't' }).cmd('ping'), (e) => e.friendly.help_topic === 'panel-does-not-appear');
  await assert.rejects(new ServiceClient(down, { port: 1, token: 't' }).health(), (e) => e.friendly.help_topic === 'service-wont-start');
});

test('service errors (401, 400) become friendly', async () => {
  const f = mockFetch(() => ({ status: 401, body: { error: { code: 'UNAUTHORIZED', user_message: 'No connection.', what_to_do: 'Reopen the panel.', help_topic: 'service-wont-start' } } }));
  await assert.rejects(new ServiceClient(f, { port: 1, token: 'x' }).preflight(), (e) => e.friendly.code === 'UNAUTHORIZED');
  const weird = mockFetch(() => ({ status: 500, body: null }));
  await assert.rejects(new ServiceClient(weird, { port: 1, token: 'x' }).preflight(), (e) => e.friendly.user_message.length > 0);
});

test('picture URLs carry the token and are encoded', () => {
  const s = new ServiceClient(async () => {}, { port: 7, token: 'a b' });
  assert.equal(s.fileUrl('j1', 'parts/eye l.png'), 'http://127.0.0.1:7/v1/jobs/j1/files/parts/eye%20l.png?token=a%20b');
});

test('run submits a job and polls until it is done, reporting the step', async () => {
  let polls = 0;
  const f = mockFetch((url, o) => {
    if (o.method === 'POST') return { status: 202, body: { job_id: 'J', status: 'queued' } };
    polls++;
    return { status: 200, body: polls < 3 ? { job_id: 'J', status: 'running', step: 'general' } : { job_id: 'J', status: 'done', manifest: { ok: 1 } } };
  });
  const steps = [];
  const r = await new ServiceClient(f, { port: 1, token: 't' }, async () => {}).run('make_layers', { sample: true }, { onStep: (s) => steps.push(s), pollMs: 0 });
  assert.deepEqual(r, { jobId: 'J', manifest: { ok: 1 } });
  assert.deepEqual(steps, ['general', 'general']);
  assert.deepEqual(JSON.parse(f.calls[0].opts.body), { type: 'make_layers', sample: true });
});

test('a cancelled job ends the wait', async () => {
  const f = mockFetch((url, o) => o.method === 'POST' ? { status: 202, body: { job_id: 'J' } } : { status: 200, body: { status: 'cancelled', manifest: { job: { status: 'cancelled' } } } });
  const r = await new ServiceClient(f, { port: 1, token: 't' }, async () => {}).run('upscale', {});
  assert.equal(r.manifest.job.status, 'cancelled');
});

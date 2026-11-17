// A stand-in for the After Effects bridge, for browser tests: same protocol as ae_host/core/bridge.cpp, canned selection,
// and it serves the panel files. exported frames are the sample character so the real service has a real picture to cut.
const http = require('http');
const fs = require('fs');
const path = require('path');

function start({ panelDir, service, sample, token }) {
  const state = {
    calls: [], selection: { has_project: true, project_id: 1, has_comp: true, comp_id: 5, comp_name: 'Main', width: 512, height: 768, fps: 24, duration: 5, time: 0,
      selected_layers: 1, has_layer: true, layer_id: 11, layer_name: 'Hero', source_kind: 'still', signature: '5:11:0' },
    errors: {}, results: {}, recovery: null,
  };
  const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.png': 'image/png', '.svg': 'image/svg+xml' };
  const send = (res, code, body, type) => { res.writeHead(code, { 'Content-Type': type || 'application/json', 'Access-Control-Allow-Origin': '*', 'Cache-Control': 'no-store' }); res.end(typeof body === 'string' || Buffer.isBuffer(body) ? body : JSON.stringify(body)); };
  const server = http.createServer((req, res) => {
    const u = new URL(req.url, 'http://x');
    if (req.method === 'POST' && u.pathname === '/v1/cmd') {
      let b = ''; req.on('data', (d) => (b += d)); req.on('end', () => {
        if (req.headers['x-voxy-token'] !== token) return send(res, 401, { ok: false, error: { code: 'UNAUTHORIZED', user_message: 'No connection.', what_to_do: 'Reopen the panel.', help_topic: 'panel-does-not-appear' } });
        const { cmd, ...args } = JSON.parse(b);
        state.calls.push({ cmd, args });
        if (state.errors[cmd]) return send(res, 200, { ok: false, error: state.errors[cmd] });
        let r = {};
        switch (cmd) {
          case 'ensure_service': r = service; break;
          case 'selection': r = state.selection; break;
          case 'export_frame': fs.mkdirSync(path.dirname(args.path), { recursive: true }); fs.copyFileSync(sample, args.path); r = { path: args.path }; break;
          case 'session_begin': r = { recovery: state.recovery }; break;
          case 'add_to_project': r = { parts_added: args.visible_ids.filter((i) => i !== 'background').length }; break;
          case 'restore': r = { more: false }; break;
          case 'save_project': r = { saved: true, time: '2026-02-03T14:05:09' }; break;
          case 'autosave': r = { status: 'saved', time: '2026-02-03T14:10:00' }; break;
          case 'apply_upscale': r = { layer_id: args.layer_id, file: '/x' }; break;
          default: r = {};
        }
        send(res, 200, { ok: true, result: state.results[cmd] || r });
      });
      return;
    }
    if (u.pathname === '/file') { const p = u.searchParams.get('p'); return fs.existsSync(p) ? send(res, 200, fs.readFileSync(p), types[path.extname(p)] || 'image/png') : send(res, 404, 'no'); }
    const f = path.join(panelDir, u.pathname === '/' ? 'index.html' : u.pathname);
    if (f.startsWith(panelDir) && fs.existsSync(f) && fs.statSync(f).isFile()) return send(res, 200, fs.readFileSync(f), types[path.extname(f)] || 'application/octet-stream');
    send(res, 404, 'not found', 'text/plain');
  });
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve({ server, port: server.address().port, state })));
}
module.exports = { start };

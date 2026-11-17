// The only file that knows whether the panel runs inside After Effects (CEP) or in a plain browser (tests, development).
// CEP specifics: Node.js access for local files, the host's window calls, and the two-window full-screen approach.
(function (root) {
  'use strict';
  const PANEL_ID = 'com.voxy.easyanimator.panel';
  const FULL_ID = 'com.voxy.easyanimator.full';

  function safeStorage(win) {
    try { const k = '__voxy_probe'; win.localStorage.setItem(k, '1'); win.localStorage.removeItem(k); return win.localStorage; }
    catch (e) { const m = {}; return { getItem: (k) => (k in m ? m[k] : null), setItem: (k, v) => { m[k] = String(v); } }; }
  }
  const joinWith = (a, b) => a + (a.indexOf('\\') >= 0 && a.indexOf('/') < 0 ? '\\' : '/') + b.replace(/\//g, a.indexOf('\\') >= 0 && a.indexOf('/') < 0 ? '\\' : '/');

  function createCep(win) {
    const nodeRequire = (win.cep_node && win.cep_node.require) || (typeof require === 'function' ? require : null);
    const fs = nodeRequire && nodeRequire('fs'), os = nodeRequire && nodeRequire('os'), cp = nodeRequire && nodeRequire('child_process'), path = nodeRequire && nodeRequire('path');
    const cep = win.__adobe_cep__;
    const isFull = (win.document.body && win.document.body.getAttribute('data-mode') === 'full');
    const dataDir = os.platform() === 'win32' ? path.join(process.env.LOCALAPPDATA || os.homedir(), 'VoxyEasyAnimator') : path.join(os.homedir(), 'Library', 'Application Support', 'VoxyEasyAnimator');
    const ensure = (d) => { try { fs.mkdirSync(d, { recursive: true }); } catch (e) { /* the next write reports it */ } };
    ensure(path.join(dataDir, 'logs')); ensure(path.join(dataDir, 'tmp', 'panel'));
    const url = (p) => 'file:///' + p.replace(/\\/g, '/').split('/').map((x, i) => (i === 0 && /^[A-Za-z]:$/.test(x) ? x : encodeURIComponent(x))).join('/');
    return {
      mode: 'cep', isFull, storage: safeStorage(win),
      readBridgeInfo() { return JSON.parse(fs.readFileSync(path.join(dataDir, 'bridge.json'), 'utf8')); },
      tempPath: (n) => path.join(dataDir, 'tmp', 'panel', n),
      fileUrl: url, joinPath: joinWith, now: () => new Date(),
      helpUrl: (topic) => url(path.join(__dirname_of(win), 'help', 'index.html')) + '#' + topic,
      openUrl(u) { try { win.cep.util.openURLInDefaultBrowser(u); } catch (e) { cp.exec('start "" "' + u + '"'); } },
      openPath(p) { cp.exec(os.platform() === 'win32' ? 'explorer /select,"' + p + '"' : 'open -R "' + p + '"'); },
      log(level, msg) {
        try { fs.appendFileSync(path.join(dataDir, 'logs', 'panel.log'), new Date().toISOString() + ' ' + level + ' ' + msg + '\n'); } catch (e) { /* nothing more to do */ }
        if (win.console) win.console[level === 'error' ? 'error' : 'log'](msg);
      },
      // Full screen: a docked panel cannot grow past its dock, so full screen is a second, floating panel sized to the
      // screen. The docked panel closes itself and the floating one picks up the same work (the session is saved).
      setFullScreen(on) {
        try {
          if (on && !isFull) { cep.requestOpenExtension(FULL_ID, ''); setTimeout(() => cep.closeExtension(), 400); }
          else if (!on && isFull) { cep.requestOpenExtension(PANEL_ID, ''); setTimeout(() => cep.closeExtension(), 400); }
        } catch (e) { /* not in a window that can do this */ }
      },
      afterStart(settings) {
        if (isFull) { try { cep.resizeContent(win.screen.availWidth, win.screen.availHeight); } catch (e) { /* keep the manifest size */ } }
      },
    };
  }
  function __dirname_of(win) { const p = decodeURIComponent(win.location.pathname).replace(/^\/([A-Za-z]:)/, '$1'); return p.replace(/[\\/][^\\/]*$/, ''); }

  // A plain browser: the page is served next to a stand-in for the After Effects bridge (?host=PORT&token=T).
  function createWeb(win) {
    const q = new URLSearchParams(win.location.search);
    const host = { port: Number(q.get('host') || 0), token: q.get('token') || '' };
    const logs = [];
    return {
      mode: 'web', isFull: false, storage: safeStorage(win), logs,
      readBridgeInfo: () => host,
      tempPath: (n) => '/tmp/voxy-web/' + n,
      fileUrl: (p) => 'http://127.0.0.1:' + host.port + '/file?p=' + encodeURIComponent(p) + '&token=' + encodeURIComponent(host.token),
      joinPath: (a, b) => a + '/' + b, now: () => new Date(),
      helpUrl: (t) => 'help/index.html#' + t,
      openUrl: (u) => { logs.push(['open', u]); (win.__opened = win.__opened || []).push(u); },
      openPath: (p) => { (win.__opened = win.__opened || []).push(p); },
      log: (l, m) => { logs.push([l, m]); },
      setFullScreen: () => {}, afterStart: () => {},
    };
  }

  function createAdapter(win) { return win.__adobe_cep__ ? createCep(win) : createWeb(win); }
  const api = { createAdapter, PANEL_ID, FULL_ID };
  if (typeof module === 'object' && module.exports) module.exports = api; else (root.Voxy = root.Voxy || {}).createAdapter = createAdapter;
})(typeof self !== 'undefined' ? self : this);

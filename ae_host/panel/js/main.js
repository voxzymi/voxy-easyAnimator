// Starts the panel: connects the pieces and draws the first screen.
(function () {
  'use strict';
  const V = window.Voxy;
  const adapter = V.createAdapter(window);
  const errors = V.errors;

  let hostClient = null;
  async function host(name, args) {
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        if (!hostClient) hostClient = new V.clients.HostClient(window.fetch.bind(window), adapter.readBridgeInfo());
        return await hostClient.cmd(name, args);
      } catch (e) {
        hostClient = null;                                         // the bridge may have restarted with a new port: read it again once
        if (attempt === 1 || (e && e.friendly && e.friendly.code !== 'HOST_DOWN')) throw (e && e.friendly ? e : errors.friendlyError(errors.local('HOST_DOWN')));
      }
    }
    return undefined;
  }

  const controller = new V.Controller({
    host: { cmd: host },
    connectService: async (info) => new V.clients.ServiceClient(window.fetch.bind(window), info),
    storage: adapter.storage, adapter,
    timers: { setInterval: window.setInterval.bind(window), clearInterval: window.clearInterval.bind(window) },
    helpUrl: adapter.helpUrl,
  });
  window.voxyController = controller;                               // handy for support: open the developer tools and look at it

  const partUrl = (state, p) => (controller.service ? controller.service.fileUrl(p.produced_by.model === 'hair' ? state.make.hairJobId : state.make.jobId, p.image) : '');
  const bgUrl = (state) => (controller.service && state.make.view && state.make.view.background ? controller.service.fileUrl(state.make.jobId, state.make.view.background.image) : '');
  V.view.mount(document.getElementById('app'), controller, {
    partUrl,
    afterDraw(state) {
      const c = document.getElementById('parts-canvas');
      if (c && state.make.view) V.canvas.draw(c, state.make, (p) => partUrl(state, p), bgUrl(state));
    },
  });

  let t = null;
  window.addEventListener('resize', () => { clearTimeout(t); t = setTimeout(() => controller.rememberSize(window.innerWidth, window.innerHeight), 400); });
  window.addEventListener('beforeunload', () => { controller.shutdown(); });
  window.addEventListener('error', (e) => adapter.log('error', String(e.error && e.error.stack || e.message)));
  window.addEventListener('unhandledrejection', (e) => adapter.log('error', String(e.reason && e.reason.stack || e.reason)));
  controller.init().then(() => adapter.afterStart(controller.state.settings));
})();

// Turns anything that goes wrong into a friendly message: what happened, what to do, and a help topic.
// Raw error text and stack traces never reach the panel; they go to the log file.
(function (root) {
  'use strict';
  const strings = typeof require === 'function' ? require('./strings.js') : root.Voxy.strings;

  function isFriendly(e) {
    return !!(e && typeof e === 'object' && typeof e.user_message === 'string' && typeof e.what_to_do === 'string' && typeof e.help_topic === 'string');
  }

  // Accepts a service/host error object, a thrown Error, or anything else.
  function present(err, log) {
    let e = err;
    if (e && e.error && isFriendly(e.error)) e = e.error;           // { error: {...} } envelopes
    if (e && e.friendly && isFriendly(e.friendly)) e = e.friendly;   // errors thrown by our API clients
    if (isFriendly(e)) {
      if (log && e.detail) log('error', (e.code || '') + ' ' + e.detail);
      return { title: e.user_message, what: e.user_message, todo: e.what_to_do, topic: e.help_topic, code: e.code || '' };
    }
    if (log) log('error', String(err && (err.stack || err.message) || err));
    const f = strings.fallbackError;
    return { title: f.user_message, what: f.user_message, todo: f.what_to_do, topic: f.help_topic, code: 'INTERNAL' };
  }

  // An Error that carries a friendly object, thrown by the API clients.
  function friendlyError(obj) {
    const e = new Error(obj && obj.code ? obj.code : 'error');
    e.friendly = isFriendly(obj) ? obj : strings.fallbackError;
    return e;
  }

  // Problems that happen before any message from the service or After Effects can arrive.
  const LOCAL = {
    SERVICE_DOWN: { code: 'SERVICE_DOWN', user_message: "Voxy's helper program is not running.", what_to_do: 'Close and reopen the panel. If it keeps happening, run the installer again.', help_topic: 'service-wont-start' },
    HOST_DOWN: { code: 'HOST_DOWN', user_message: 'Voxy could not reach After Effects.', what_to_do: 'Close and reopen the panel. If it keeps happening, restart After Effects.', help_topic: 'panel-does-not-appear' },
    NOTHING_TO_ADD: { code: 'NOTHING_TO_ADD', user_message: 'Every part is hidden, so there is nothing to add.', what_to_do: 'Turn on the eye of at least one part.', help_topic: 'eye-buttons' },
    NO_PARTS: { code: 'NO_PARTS', user_message: 'There are no parts yet.', what_to_do: 'Press Make Layers first.', help_topic: 'first-run' },
    NOT_A_PICTURE_LAYER: { code: 'NOT_A_PICTURE_LAYER', user_message: 'Upscale 2x works on picture layers.', what_to_do: 'Select a layer that is a still picture, such as a PNG or PSD layer.', help_topic: 'upscale' },
    NO_LAYER: { code: 'NO_LAYER', user_message: 'No layer is selected.', what_to_do: 'Click your character layer in the timeline.', help_topic: 'no-layer' },
    SAMPLE_ONLY: { code: 'SAMPLE_ONLY', user_message: 'The sample character is only a preview.', what_to_do: 'Select your own layer and press Make Layers to add parts to your project.', help_topic: 'first-run' },
  };
  const local = (code) => LOCAL[code];

  const api = { isFriendly, present, friendlyError, LOCAL, local };
  if (typeof module === 'object' && module.exports) module.exports = api; else (root.Voxy = root.Voxy || {}).errors = api;
})(typeof self !== 'undefined' ? self : this);

// Remembers previews so going back to a layer does not process it again.
(function (root) {
  'use strict';
  class PreviewCache {
    constructor(limit) { this.limit = limit || 12; this.map = new Map(); this.hits = 0; this.misses = 0; }
    get(key) {
      if (!this.map.has(key)) { this.misses++; return undefined; }
      const v = this.map.get(key);
      this.map.delete(key); this.map.set(key, v);                    // most recently used goes last
      this.hits++;
      return v;
    }
    set(key, value) {
      if (this.map.has(key)) this.map.delete(key);
      this.map.set(key, value);
      while (this.map.size > this.limit) this.map.delete(this.map.keys().next().value);
    }
    has(key) { return this.map.has(key); }
    delete(key) { this.map.delete(key); }
    clear() { this.map.clear(); }
    get size() { return this.map.size; }
  }
  if (typeof module === 'object' && module.exports) module.exports = PreviewCache; else (root.Voxy = root.Voxy || {}).PreviewCache = PreviewCache;
})(typeof self !== 'undefined' ? self : this);

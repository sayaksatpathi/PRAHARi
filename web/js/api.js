/* Prahari API client.
 *
 * Thin, dependency-free wrapper over the node's REST API plus the WebSocket
 * feed. Kept separate from rendering so the transport can change - a future
 * build may talk to a sector core instead of a single node - without touching
 * any view code.
 */
'use strict';

const API = (() => {
  const TOKEN_KEY = 'prahari.token';
  let token = null;
  try { token = localStorage.getItem(TOKEN_KEY); } catch (_) { token = null; }

  let user = null;
  let ws = null;
  let wsRetry = 0;
  const listeners = new Map();          // subject prefix -> [handler]

  function setToken(value, principal) {
    token = value;
    user = principal || null;
    try {
      if (value) localStorage.setItem(TOKEN_KEY, value);
      else localStorage.removeItem(TOKEN_KEY);
    } catch (_) { /* private browsing; session-only is fine */ }
  }

  async function request(path, options = {}) {
    const opts = Object.assign({ headers: {} }, options);
    if (token) opts.headers['Authorization'] = 'Bearer ' + token;
    if (opts.body !== undefined && typeof opts.body !== 'string') {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(opts.body);
    }
    const res = await fetch(path, opts);
    if (res.status === 401) {
      setToken(null);
      document.dispatchEvent(new CustomEvent('prahari:unauthenticated'));
      throw new Error('authentication required');
    }
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch (_) {}
      throw new Error(detail);
    }
    if (res.status === 204) return null;
    const type = res.headers.get('content-type') || '';
    return type.includes('application/json') ? res.json() : res.blob();
  }

  /* --- auth ------------------------------------------------------------ */
  async function login(username, password) {
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    if (!res.ok) {
      let msg = 'Sign-in failed';
      try { msg = (await res.json()).detail || msg; } catch (_) {}
      throw new Error(msg);
    }
    const data = await res.json();
    setToken(data.token, { username: data.username, role: data.role });
    return data;
  }

  async function me() {
    const data = await request('/api/auth/me');
    user = data;
    return data;
  }

  function logout() {
    setToken(null);
    if (ws) { try { ws.close(); } catch (_) {} ws = null; }
  }

  /* --- websocket -------------------------------------------------------- */
  function connect() {
    if (!token) return;
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    try { ws = new WebSocket(`${proto}//${location.host}/ws`); }
    catch (_) { return; }

    ws.onopen = () => { wsRetry = 0; emit('ws:open', {}); };
    ws.onmessage = (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch (_) { return; }
      emit(msg.subject, msg.data);
      // Also emit a coarse kind so views can subscribe without knowing the
      // node id embedded in the subject.
      const parts = (msg.subject || '').split('.');
      if (parts.length >= 3) emit('kind:' + parts[2], msg.data);
    };
    ws.onclose = () => {
      ws = null;
      emit('ws:close', {});
      // Back off, but never give up: the operator's screen reconnecting on its
      // own matters more here than anywhere, because nobody is going to press
      // refresh at 03:00.
      wsRetry = Math.min(wsRetry + 1, 6);
      setTimeout(connect, 500 * Math.pow(2, wsRetry));
    };
    ws.onerror = () => { try { ws.close(); } catch (_) {} };
  }

  function on(subject, handler) {
    if (!listeners.has(subject)) listeners.set(subject, []);
    listeners.get(subject).push(handler);
  }

  function emit(subject, data) {
    const handlers = listeners.get(subject);
    if (handlers) handlers.forEach(h => { try { h(data); } catch (e) { console.error(e); } });
  }

  /* --- endpoints -------------------------------------------------------- */
  return {
    login, logout, me, connect, on,
    get token() { return token; },
    get user() { return user; },

    status:      () => request('/api/system/status'),
    bandwidth:   () => request('/api/system/bandwidth'),
    verifyChain: () => request('/api/system/ledger/verify'),
    audit:       (limit = 200) => request(`/api/system/audit?limit=${limit}`),

    cameras:     () => request('/api/cameras'),
    camera:      (id) => request('/api/cameras/' + encodeURIComponent(id)),
    certificate: (id) => request(`/api/cameras/${encodeURIComponent(id)}/certificate`),
    testCamera:  (id) => request(`/api/cameras/${encodeURIComponent(id)}/test`, { method: 'POST' }),
    reprofile:   (id) => request(`/api/cameras/${encodeURIComponent(id)}/profile`, { method: 'POST' }),
    streamUrl:   (id) => `/api/cameras/${encodeURIComponent(id)}/stream`,

    zones:       (cameraId) => request('/api/zones' + (cameraId ? '?camera_id=' + encodeURIComponent(cameraId) : '')),

    events:      (params = {}) => {
      const q = new URLSearchParams(params).toString();
      return request('/api/events' + (q ? '?' + q : ''));
    },
    event:       (id) => request('/api/events/' + encodeURIComponent(id)),
    evidenceUrl: (id, kind) => `/api/events/${encodeURIComponent(id)}/evidence/${kind}`,

    acknowledge: (id, feedback, note) =>
      request(`/api/alerts/${encodeURIComponent(id)}/acknowledge`,
              { method: 'POST', body: { feedback: feedback || null, note: note || null } }),
    feedback:    () => request('/api/alerts/feedback'),

    demo:        (action, extra = {}) =>
      request('/api/demo/action', { method: 'POST', body: Object.assign({ action }, extra) }),
  };
})();

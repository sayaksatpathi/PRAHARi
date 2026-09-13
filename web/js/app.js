/* Prahari application shell: auth gate, routing, and the shared state that
 * views read from.
 *
 * State is pushed by the node over the WebSocket rather than polled. Views
 * re-paint from whatever is already in memory, so switching tabs is instant and
 * a slow network degrades freshness rather than usability.
 */
'use strict';

const App = (() => {
  const state = {
    status: null,
    cameras: [],
    alerts: [],
    selectedCamera: null,
  };

  let currentView = 'dashboard';
  let seenEventIds = new Set();
  let firstLoad = true;

  /* --- routing ---------------------------------------------------------- */
  async function go(view) {
    currentView = view;
    document.querySelectorAll('.nav-item').forEach(n =>
      n.classList.toggle('active', n.dataset.view === view));
    const el = document.getElementById('view');
    el.innerHTML = '<div class="empty">Loading…</div>';
    try {
      await Views[view].render(el, state);
    } catch (e) {
      console.error(e);
      el.innerHTML = `<div class="notice bad">Could not render this view: ${UI.esc(e.message)}</div>`;
    }
  }

  function repaint() {
    const view = Views[currentView];
    if (view && typeof view.paint === 'function') {
      try { view.paint(state); } catch (e) { console.error(e); }
    }
  }

  /* --- data ------------------------------------------------------------- */
  async function refresh({ full = false } = {}) {
    try {
      const [status, cams, evs] = await Promise.all([
        API.status(),
        API.cameras(),
        API.events({ limit: 300 }),
      ]);
      state.status = status;
      state.cameras = cams.cameras || [];
      state.alerts = evs.events || [];
      applyChrome();
      if (full) await go(currentView); else repaint();
      announceNew();
    } catch (e) {
      if (e.message !== 'authentication required') console.error(e);
    }
  }

  function announceNew() {
    // Only speak up about genuinely new, genuinely raised alerts. The first
    // load is silent - an operator opening the console should not be greeted by
    // forty toasts about things that happened before they sat down.
    const fresh = state.alerts.filter(e =>
      e.alerted && !seenEventIds.has(e.event_id) &&
      ['high', 'critical'].includes(e.priority));
    state.alerts.forEach(e => seenEventIds.add(e.event_id));
    if (firstLoad) { firstLoad = false; return; }
    fresh.slice(0, 3).forEach(e =>
      UI.toast(`${e.priority.toUpperCase()} — ${e.summary}`, 'bad', 7000));
  }

  /* --- chrome ----------------------------------------------------------- */
  function applyChrome() {
    const s = state.status;
    if (!s) return;
    const link = UI.linkBadge(s.link_mode);
    const alerting = s.alerting || {};

    setBadge('tb-node', 'b-neutral', `${s.node_id}`);
    const linkEl = document.getElementById('tb-link');
    linkEl.className = `badge ${link.cls}`;
    linkEl.innerHTML = `<span class="dot"></span><span>${UI.esc(link.label)}</span>`;

    const overBudget = (s.alerts_last_hour ?? 0) >= (s.alert_budget_per_hour ?? 99);
    setBadge('tb-alerts', overBudget ? 'b-warn' : 'b-neutral',
             `${s.alerts_last_hour ?? 0}/${s.alert_budget_per_hour ?? 0} alerts/h`);
    setBadge('tb-cameras', 'b-neutral',
             `${s.cameras_online ?? 0}/${s.cameras_total ?? 0} cameras`);
    setBadge('tb-queue', (s.events_pending_sync ?? 0) > 0 ? 'b-warn' : 'b-ok',
             `${s.events_pending_sync ?? 0} queued`);

    const det = s.detector || {};
    setBadge('tb-detector', det.simulated ? 'b-warn' : 'b-ok',
             det.simulated ? 'SIMULATED DETECTOR' : `${(det.name || '').toUpperCase()} · ${(det.device || '').toUpperCase()}`);

    document.getElementById('tb-user').textContent =
      API.user ? `${API.user.username} (${API.user.role})` : '';

    const navCount = document.getElementById('nav-cam-count');
    if (navCount) navCount.textContent = `${s.cameras_online ?? 0}/${s.cameras_total ?? 0}`;

    const unacked = state.alerts.filter(e => e.alerted && !e.acknowledged).length;
    const navAlert = document.getElementById('nav-alert-count');
    navAlert.style.display = unacked ? '' : 'none';
    navAlert.textContent = unacked;
  }

  function setBadge(id, cls, text) {
    const el = document.getElementById(id);
    if (!el) return;
    el.className = `badge ${cls}${id === 'tb-node' ? ' mono' : ''}`;
    el.textContent = text;
  }

  function tickClock() {
    const el = document.getElementById('tb-clock');
    if (el) el.textContent = new Date().toLocaleTimeString('en-GB', { hour12: false }) + ' local';
  }

  /* --- auth gate -------------------------------------------------------- */
  function showLogin(message) {
    document.getElementById('login').style.display = '';
    document.getElementById('app').style.display = 'none';
    const err = document.getElementById('login-error');
    if (message) { err.textContent = message; err.style.display = ''; }
    else err.style.display = 'none';
  }

  function showApp() {
    document.getElementById('login').style.display = 'none';
    document.getElementById('app').style.display = '';
  }

  async function start() {
    document.getElementById('login-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const u = document.getElementById('lg-user').value;
      const p = document.getElementById('lg-pass').value;
      try {
        await API.login(u, p);
        await boot();
      } catch (err) {
        showLogin(err.message);
      }
    });

    document.getElementById('tb-logout').addEventListener('click', () => {
      API.logout();
      seenEventIds = new Set();
      firstLoad = true;
      showLogin();
    });

    document.querySelectorAll('.nav-item').forEach(n =>
      n.addEventListener('click', () => go(n.dataset.view)));

    document.addEventListener('prahari:unauthenticated', () =>
      showLogin('Your session has expired. Please sign in again.'));
    document.addEventListener('prahari:refresh', () => refresh());

    setInterval(tickClock, 1000);
    tickClock();

    if (API.token) {
      try { await API.me(); await boot(); }
      catch (_) { showLogin(); }
    } else {
      showLogin();
    }
  }

  async function boot() {
    showApp();
    await refresh({ full: true });
    API.connect();

    // Live pushes. Status arrives every couple of seconds; events and tracks
    // arrive as they happen.
    API.on('system.status', (data) => {
      state.status = Object.assign({}, state.status, data);
      applyChrome();
      repaint();
    });
    API.on('kind:events', () => refresh());
    API.on('ws:close', () => {
      const el = document.getElementById('tb-link');
      if (el) { el.className = 'badge b-warn'; el.innerHTML = '<span class="dot"></span><span>RECONNECTING</span>'; }
    });

    // Safety net: if the socket is wedged, the console must still be current.
    setInterval(() => refresh(), 15000);
  }

  return { go, refresh, state, start };
})();

document.addEventListener('DOMContentLoaded', App.start);

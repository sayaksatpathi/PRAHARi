/* Small rendering helpers shared across views.
 *
 * No framework. The dashboard is a handful of panels refreshed from a
 * WebSocket, and a build step would buy nothing here while costing the ability
 * to recover the interface from a USB stick on a node with no internet.
 */
'use strict';

const UI = (() => {

  /* --- escaping --------------------------------------------------------- */
  // Every string that reaches the DOM goes through this. Camera names, zone
  // names and operator notes are operator-supplied and must never be able to
  // inject markup into a console someone is trusting.
  function esc(value) {
    if (value === null || value === undefined) return '';
    return String(value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function h(html) {
    const t = document.createElement('template');
    t.innerHTML = html.trim();
    return t.content.firstElementChild;
  }

  /* --- formatting -------------------------------------------------------- */
  function bytes(n) {
    if (n === null || n === undefined) return '—';
    const units = ['B', 'kB', 'MB', 'GB', 'TB'];
    let v = Number(n), i = 0;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return `${v < 10 && i > 0 ? v.toFixed(1) : Math.round(v)} ${units[i]}`;
  }

  function bitrate(bps) {
    if (!bps) return '0 bit/s';
    const units = ['bit/s', 'kbit/s', 'Mbit/s', 'Gbit/s'];
    let v = Number(bps), i = 0;
    while (v >= 1000 && i < units.length - 1) { v /= 1000; i++; }
    return `${v < 10 ? v.toFixed(1) : Math.round(v)} ${units[i]}`;
  }

  function duration(seconds) {
    const s = Math.max(0, Math.floor(seconds || 0));
    if (s < 60) return s + 's';
    if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
    return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
  }

  function timeOf(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    return d.toLocaleTimeString('en-GB', { hour12: false });
  }

  function dateTimeOf(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    return d.toLocaleString('en-GB', { hour12: false });
  }

  function ago(iso) {
    if (!iso) return '—';
    const delta = (Date.now() - new Date(iso).getTime()) / 1000;
    if (delta < 60) return Math.max(0, Math.floor(delta)) + 's ago';
    if (delta < 3600) return Math.floor(delta / 60) + 'm ago';
    return Math.floor(delta / 3600) + 'h ago';
  }

  function titleise(s) {
    return String(s || '').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
  }

  /* --- severity ---------------------------------------------------------- */
  const SEV_CLASS = {
    critical: 'b-crit', high: 'b-high', medium: 'b-med',
    low: 'b-low', info: 'b-info',
  };

  function priorityBadge(priority, solid) {
    const cls = SEV_CLASS[priority] || 'b-info';
    // The text label is not decorative: severity must survive a monochrome
    // screen and a colour-blind operator.
    return `<span class="badge ${solid ? 'solid ' : ''}${cls}"><span class="dot"></span>${esc(String(priority).toUpperCase())}</span>`;
  }

  const LINK_CLASS = {
    online: 'b-ok', syncing: 'b-accent', degraded: 'b-warn',
    event_only: 'b-warn', offline: 'b-bad',
  };

  function linkBadge(mode) {
    const cls = LINK_CLASS[mode] || 'b-neutral';
    return { cls, label: titleise(mode).toUpperCase() };
  }

  function healthBadge(health) {
    const map = { online: 'b-ok', degraded: 'b-warn', offline: 'b-bad',
                  tampered: 'b-crit', unknown: 'b-neutral' };
    return `<span class="badge ${map[health] || 'b-neutral'}"><span class="dot"></span>${esc(String(health).toUpperCase())}</span>`;
  }

  /* --- toast ------------------------------------------------------------- */
  function toast(message, kind = 'ok', ms = 4200) {
    const el = h(`<div class="toast ${kind}">${esc(message)}</div>`);
    document.getElementById('toast').appendChild(el);
    setTimeout(() => el.remove(), ms);
  }

  /* --- modal ------------------------------------------------------------- */
  function modal(titleHtml, bodyHtml, onMount) {
    closeModal();
    const backdrop = h(`
      <div class="modal-backdrop">
        <div class="modal">
          <div class="modal-head">
            <div style="flex:1">${titleHtml}</div>
            <button class="sm" data-close>Close</button>
          </div>
          <div class="panel-body" data-body></div>
        </div>
      </div>`);
    backdrop.querySelector('[data-body]').innerHTML = bodyHtml;
    backdrop.addEventListener('click', (e) => {
      if (e.target === backdrop || e.target.hasAttribute('data-close')) closeModal();
    });
    document.getElementById('modal-root').appendChild(backdrop);
    if (onMount) onMount(backdrop);
    return backdrop;
  }

  function closeModal() {
    document.getElementById('modal-root').innerHTML = '';
    // Let modal content release resources (e.g. revoke evidence object URLs).
    document.dispatchEvent(new CustomEvent('prahari:modal-close'));
  }

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeModal();
  });

  /* --- priority factor bars ---------------------------------------------- */
  function factorBars(factors) {
    if (!factors || !factors.length) {
      return '<p class="small faint">No contributing factors recorded.</p>';
    }
    const max = Math.max(...factors.map(f => Math.abs(f.weight)), 0.3);
    return factors.map(f => {
      const pct = Math.min(50, Math.abs(f.weight) / max * 50);
      const positive = f.weight >= 0;
      return `
        <div class="factor">
          <div class="factor-name">${esc(f.name)}</div>
          <div class="factor-bar">
            <div class="factor-fill ${positive ? 'pos' : 'neg'}" style="width:${pct}%"></div>
          </div>
          <div class="factor-val" style="color:${positive ? 'var(--high)' : 'var(--ok)'}">
            ${f.weight >= 0 ? '+' : ''}${f.weight.toFixed(2)}
          </div>
          ${f.detail ? `<div class="factor-detail">${esc(f.detail)}</div>` : ''}
        </div>`;
    }).join('');
  }

  /* --- sector map --------------------------------------------------------
   * Hand-drawn SVG rather than a mapping library. A tile-serving map needs the
   * internet, and this interface has to render on a node that has not seen the
   * internet in a week. Camera positions are plotted from their own lat/lon on
   * a local equirectangular projection, which at the scale of one outpost is
   * accurate enough to be useful and honest about being a schematic.
   */
  function sectorMap(cameras, selectedId) {
    const pts = cameras.filter(c => c.latitude != null && c.longitude != null);
    if (!pts.length) {
      return '<div class="empty">No camera positions configured.</div>';
    }
    const W = 900, H = 340, PAD = 46;
    const lats = pts.map(p => p.latitude), lons = pts.map(p => p.longitude);
    const minLat = Math.min(...lats), maxLat = Math.max(...lats);
    const minLon = Math.min(...lons), maxLon = Math.max(...lons);
    const spanLat = Math.max(maxLat - minLat, 0.004);
    const spanLon = Math.max(maxLon - minLon, 0.004);

    const x = lon => PAD + ((lon - minLon) / spanLon) * (W - 2 * PAD);
    const y = lat => H - PAD - ((lat - minLat) / spanLat) * (H - 2 * PAD);

    const grid = [];
    for (let i = 0; i <= 6; i++) {
      const gx = PAD + (i / 6) * (W - 2 * PAD);
      const gy = PAD + (i / 6) * (H - 2 * PAD);
      grid.push(`<line x1="${gx}" y1="${PAD}" x2="${gx}" y2="${H - PAD}" stroke="#131b24"/>`);
      grid.push(`<line x1="${PAD}" y1="${gy}" x2="${W - PAD}" y2="${gy}" stroke="#131b24"/>`);
    }

    const markers = pts.map(c => {
      const cx = x(c.longitude), cy = y(c.latitude);
      const rt = c.runtime || {};
      const colour = rt.health === 'online' ? 'var(--ok)'
                   : rt.health === 'tampered' ? 'var(--crit)'
                   : rt.health === 'offline' ? 'var(--bad)' : 'var(--text-faint)';
      const selected = c.camera_id === selectedId;
      return `
        <g class="map-cam" data-camera="${esc(c.camera_id)}">
          ${selected ? `<circle cx="${cx}" cy="${cy}" r="17" fill="none" stroke="var(--accent)" stroke-dasharray="3 3"/>` : ''}
          <circle cx="${cx}" cy="${cy}" r="7" fill="${colour}" fill-opacity="0.22" stroke="${colour}" stroke-width="1.6"/>
          <text x="${cx + 11}" y="${cy - 6}" fill="var(--text)" font-size="11"
                font-family="ui-monospace, monospace">${esc(c.camera_id)}</text>
          <text x="${cx + 11}" y="${cy + 6}" fill="var(--text-faint)" font-size="9.5">
            ${esc((c.role || '').toUpperCase())}
          </text>
        </g>`;
    }).join('');

    const scaleKm = (spanLon * 111 * Math.cos(minLat * Math.PI / 180)).toFixed(2);

    return `
      <svg class="sector-map" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">
        ${grid.join('')}
        <rect x="${PAD}" y="${PAD}" width="${W - 2 * PAD}" height="${H - 2 * PAD}"
              fill="none" stroke="#1c2531"/>
        ${markers}
        <text x="${PAD}" y="${H - 16}" fill="var(--text-faint)" font-size="10">
          Schematic sector plot — approximately ${scaleKm} km across. Not a survey map.
        </text>
      </svg>`;
  }

  return {
    esc, h, bytes, bitrate, duration, timeOf, dateTimeOf, ago, titleise,
    priorityBadge, linkBadge, healthBadge, toast, modal, closeModal,
    factorBars, sectorMap,
  };
})();

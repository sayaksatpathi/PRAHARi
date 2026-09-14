/* Prahari views.
 *
 * Each view exports render(container, state) and may register a refresh hook.
 * State is shared and refreshed from the WebSocket, so views read rather than
 * fetch wherever possible - an operator switching tabs should never wait.
 */
'use strict';

const Views = (() => {
  const { esc, h, bytes, bitrate, duration, timeOf, dateTimeOf, ago, titleise,
          priorityBadge, healthBadge, factorBars, sectorMap, modal, toast } = UI;

  /* ==================================================================
   * Shared fragments
   * ================================================================== */

  function detectorNotice(state) {
    const d = (state.status && state.status.detector) || {};
    if (!d.simulated) return '';
    return `
      <div class="notice warn" style="margin-bottom:12px">
        <strong>Simulated detector in use.</strong>
        ${esc(d.note || '')}
        Install an ONNX model (see <span class="mono">models/README.md</span>) to
        run real inference.
      </div>`;
  }

  function statTile(label, value, sub, valueColour) {
    return `
      <div class="stat">
        <div class="stat-label">${esc(label)}</div>
        <div class="stat-value" ${valueColour ? `style="color:${valueColour}"` : ''}>${value}</div>
        ${sub ? `<div class="stat-sub">${sub}</div>` : ''}
      </div>`;
  }

  function cameraCard(cam, opts = {}) {
    const rt = cam.runtime || {};
    const granted = rt.granted || [];
    const profiling = rt.state === 'profiling';
    const pg = rt.profiling_progress;

    const caps = granted.length
      ? granted.slice(0, 7).map(c => `<span class="cap granted">${esc(c.replace(/_/g, ' '))}</span>`).join('')
      : '<span class="cap refused">no analytics granted yet</span>';

    return `
      <div class="cam-card" data-camera="${esc(cam.camera_id)}">
        <div class="cam-video">
          ${opts.live
            ? `<img src="${API.streamUrl(cam.camera_id)}" alt="Live view from ${esc(cam.camera_id)}"
                    onerror="this.style.display='none'">`
            : `<div class="placeholder">Preview disabled on this view</div>`}
          <div class="cam-overlay">
            <span class="badge b-neutral mono tiny">${esc(cam.camera_id)}</span>
            ${healthBadge(rt.health || cam.health)}
            <span class="badge b-neutral tiny">${esc((rt.fps ?? 0).toFixed ? rt.fps.toFixed(1) : rt.fps)} fps</span>
            <span class="badge b-neutral tiny">${esc(rt.tracks ?? 0)} tracked</span>
            ${profiling ? `<span class="badge b-accent tiny">PROFILING</span>` : ''}
          </div>
        </div>
        <div class="cam-meta">
          <div class="cam-name">${esc(cam.name)}</div>
          <div class="tiny dim">${esc(cam.location || '')}</div>
          <div class="row wrap tiny" style="margin-top:6px">
            <span class="badge b-neutral tiny">${esc(titleise(cam.role))}</span>
            <span class="badge ${cam.border_profile === 'open' ? 'b-accent' : 'b-neutral'} tiny">
              ${esc(cam.border_profile === 'open' ? 'OPEN BORDER' : 'FENCED')}
            </span>
            ${rt.dori ? `<span class="badge b-neutral tiny" title="IEC 62676-4 pixels-on-target band">DORI ${esc(rt.dori.toUpperCase())}</span>` : ''}
          </div>
          ${rt.detector_blind ? `
            <div class="notice bad tiny" style="margin-top:7px">
              <strong>Detecting nothing.</strong> ${esc(rt.detector_note || '')}
            </div>` : ''}
          ${profiling && pg
            ? `<div class="tiny faint" style="margin-top:6px">
                 Measuring optics; needs ${pg.ground_samples_required - pg.ground_samples} more
                 person observations to fit the ground plane
                 (${pg.ground_samples}/${pg.ground_samples_required}).
               </div>`
            : `<div class="cam-caps">${caps}</div>`}
        </div>
      </div>`;
  }

  const PATROL_BADGE = {
    suppressed: ['b-ok', 'PATROL MATCH'],
    downgraded: ['b-warn', 'PATROL — UNCERTAIN'],
    deviation:  ['b-high', 'PATROL DEVIATION'],
  };

  function patrolBadge(patrol, tiny) {
    if (!patrol || patrol.decision === 'not_matched') return '';
    const [cls, label] = PATROL_BADGE[patrol.decision] || ['b-neutral', 'PATROL'];
    return `<span class="badge ${cls} ${tiny ? 'tiny' : ''}"
                  title="${esc(patrol.reason || '')}">${label}</span>`;
  }

  /* Expected versus observed, side by side. "Suppressed, trust me" is not a
   * reviewable statement, so the card always shows what the patrol was
   * supposed to be doing next to what was actually seen. */
  function patrolPanel(patrol) {
    if (!patrol) return '';
    const cues = (patrol.cues || []).map(c => `
      <tr class="${c.evaluable ? '' : 'faint'}">
        <td>${esc(titleise(c.name))}</td>
        <td class="mono">${c.evaluable ? Number(c.score).toFixed(2) : '—'}</td>
        <td class="tiny" style="font-family:var(--sans)">${esc(c.detail)}</td>
      </tr>`).join('');

    const devs = (patrol.deviations || []).length ? `
      <div class="notice bad" style="margin:10px 0 0">
        <strong>Deviation from the declared patrol:</strong>
        ${esc(patrol.deviations.join('; '))}.
      </div>` : '';

    return `
      <div class="panel" style="margin-bottom:12px">
        <div class="panel-head">
          <span class="panel-title">Friendly force</span>
          ${patrolBadge(patrol, true)}
        </div>
        <div class="panel-body">
          ${patrol.decision === 'not_matched' ? `
            <p class="small dim" style="margin-top:0">
              No declared patrol accounts for this event, so it took the normal
              alert path. ${esc(patrol.reason || '')}
            </p>` : `
            <dl class="kv">
              <dt>Decision</dt><dd>${esc(titleise(patrol.decision))}</dd>
              <dt>Patrol</dt><dd>${esc(patrol.patrol_name || patrol.patrol_id || '—')}</dd>
              <dt>Match score</dt><dd class="mono">${Number(patrol.match_score).toFixed(2)}
                <span class="tiny faint">(identity ${Number(patrol.identity_confidence).toFixed(2)},
                conformance ${Number(patrol.conformance).toFixed(2)})</span></dd>
              <dt>Expected route</dt><dd class="mono">${esc((patrol.expected_route || []).join(' → ')) || '—'}</dd>
              <dt>Expected window</dt><dd class="mono">${esc(patrol.expected_window || '—')}</dd>
              <dt>Observed time</dt><dd class="mono">${patrol.observed_time ? dateTimeOf(patrol.observed_time) : '—'}</dd>
              <dt>Expected direction</dt><dd class="mono">${patrol.expected_heading_deg != null ? Number(patrol.expected_heading_deg).toFixed(0) + '°' : '—'}</dd>
              <dt>Observed direction</dt><dd class="mono">${patrol.observed_heading_deg != null ? Number(patrol.observed_heading_deg).toFixed(0) + '°' : 'not measurable'}</dd>
              <dt>Score effect</dt><dd class="mono">${Number(patrol.score_adjustment) >= 0 ? '+' : ''}${Number(patrol.score_adjustment).toFixed(2)}</dd>
            </dl>
            ${devs}
            <p class="small" style="font-family:var(--sans)">${esc(patrol.reason || '')}</p>
            <div class="hr"></div>
            <table class="tiny"><thead><tr><th>Cue</th><th>Agreement</th><th>Basis</th></tr></thead>
              <tbody>${cues}</tbody></table>`}
          <p class="tiny faint" style="margin-bottom:0">
            A suppressed event is recorded, scored, sealed into the hash chain
            with this assessment attached, and synchronised. It is never
            deleted or hidden — it simply did not interrupt anybody.
          </p>
        </div>
      </div>`;
  }

  function alertRow(ev, isNew) {
    const acked = ev.acknowledged;
    return `
      <div class="alert sev-${esc(ev.priority)} ${acked ? 'acked' : ''} ${isNew ? 'alert-new' : ''}"
           data-event="${esc(ev.event_id)}">
        <div class="alert-head">
          ${priorityBadge(ev.priority)}
          <span class="badge b-neutral mono tiny">${esc(ev.camera_id)}</span>
          <span class="tiny dim">${esc(titleise(ev.event_type))}</span>
          ${patrolBadge(ev.patrol, true)}
          <span class="right tiny mono dim">${timeOf(ev.timestamp)}</span>
        </div>
        <div class="alert-summary">${esc(ev.summary)}</div>
        <div class="alert-foot">
          ${ev.zone_name ? `<span>${esc(ev.zone_name)}</span>` : ''}
          ${ev.track_id != null ? `<span class="mono">${esc(ev.object_class)} #${esc(ev.track_id)}</span>` : ''}
          <span class="mono">score ${Number(ev.priority_score).toFixed(2)}</span>
          ${ev.evidence && ev.evidence.clip_path ? '<span>clip</span>' : ''}
          ${!ev.alerted ? '<span class="faint">recorded, not alerted</span>' : ''}
          ${acked ? `<span class="faint">ack ${esc(ev.acknowledged_by || '')}</span>` : ''}
          ${ev.sync_state === 'pending' ? '<span class="badge b-warn tiny">QUEUED</span>' : ''}
        </div>
      </div>`;
  }

  /* ==================================================================
   * Event detail modal
   * ================================================================== */

  async function openEvent(eventId) {
    let ev;
    try { ev = await API.event(eventId); }
    catch (e) { return toast('Could not load event: ' + e.message, 'bad'); }

    const evd = ev.evidence || {};
    const hasClip = !!evd.clip_path;

    const body = `
      <div class="grid g2">
        <div>
          <div class="panel" style="margin-bottom:12px">
            <div class="panel-head"><span class="panel-title">Evidence</span>
              ${hasClip ? '<span class="badge b-ok tiny">CLIP + PRE-ROLL</span>'
                        : '<span class="badge b-warn tiny">FRAME ONLY</span>'}
            </div>
            <div class="panel-body flush">
              ${hasClip
                ? `<video src="${API.evidenceUrl(ev.event_id, 'clip')}" controls
                          style="width:100%;display:block;background:#05080c"></video>`
                : `<img src="${API.evidenceUrl(ev.event_id, 'frame')}" alt="Trigger frame"
                        style="width:100%;display:block">`}
            </div>
          </div>

          <div class="panel">
            <div class="panel-head"><span class="panel-title">Integrity</span></div>
            <div class="panel-body">
              <dl class="kv">
                <dt>Ledger position</dt><dd>${ev.ledger_index ? '#' + ev.ledger_index : 'not yet sealed'}</dd>
                <dt>Entry hash</dt><dd class="tiny">${esc((ev.entry_hash || '—').slice(0, 40))}…</dd>
                <dt>Frame SHA-256</dt><dd class="tiny">${esc((evd.frame_sha256 || '—').slice(0, 40))}…</dd>
                ${evd.clip_sha256 ? `<dt>Clip SHA-256</dt><dd class="tiny">${esc(evd.clip_sha256.slice(0, 40))}…</dd>` : ''}
                <dt>Sync state</dt><dd>${esc(ev.sync_state)}</dd>
                <dt>Clock trusted</dt><dd>${ev.clock_synced ? 'yes' : 'no — corrected at core'}</dd>
              </dl>
            </div>
          </div>
        </div>

        <div>
          <div class="panel" style="margin-bottom:12px">
            <div class="panel-head"><span class="panel-title">Why this scored ${Number(ev.priority_score).toFixed(2)}</span></div>
            <div class="panel-body">
              ${factorBars(ev.priority_factors)}
              <div class="hr"></div>
              <p class="tiny faint" style="margin:0">
                The Event Priority Score is a ranking aid, not a calibrated
                probability. It has not been validated against border ground
                truth and must not be read as a likelihood of intrusion.
              </p>
            </div>
          </div>

          ${ev.patrol ? patrolPanel(ev.patrol) : ''}

          <div class="panel" style="margin-bottom:12px">
            <div class="panel-head"><span class="panel-title">Detail</span></div>
            <div class="panel-body">
              <dl class="kv">
                <dt>Event</dt><dd>${esc(ev.event_id)}</dd>
                <dt>Type</dt><dd>${esc(titleise(ev.event_type))}</dd>
                <dt>Camera</dt><dd>${esc(ev.camera_id)}</dd>
                <dt>Zone</dt><dd>${esc(ev.zone_name || '—')}</dd>
                <dt>Object</dt><dd>${esc(ev.object_class)}${ev.track_id != null ? ' #' + esc(ev.track_id) : ''}</dd>
                <dt>Detector confidence</dt><dd>${(Number(ev.confidence) * 100).toFixed(0)}%</dd>
                <dt>Observed</dt><dd>${dateTimeOf(ev.timestamp)}</dd>
                ${ev.alert_decision ? `<dt>Alerting</dt><dd style="font-family:var(--sans)">${esc(ev.alert_decision)}</dd>` : ''}
                ${ev.detail && ev.detail.normalcy ? `<dt>Pattern of life</dt><dd style="font-family:var(--sans)">${esc(ev.detail.normalcy)}</dd>` : ''}
              </dl>
            </div>
          </div>

          <div class="panel">
            <div class="panel-head"><span class="panel-title">Operator action</span></div>
            <div class="panel-body">
              ${ev.acknowledged
                ? `<p class="small dim" style="margin-top:0">
                     Acknowledged by <span class="mono">${esc(ev.acknowledged_by)}</span>
                     ${ago(ev.acknowledged_at)}${ev.operator_feedback ? ' — marked <strong>' + esc(titleise(ev.operator_feedback)) + '</strong>' : ''}.
                   </p>`
                : `<p class="small dim" style="margin-top:0">
                     Marking an alert a false alarm feeds back into this camera's
                     scoring, so repeated false alarms are damped automatically.
                   </p>
                   <div class="row wrap">
                     <button class="good" data-ack="true_positive">Confirm — genuine</button>
                     <button data-ack="false_alarm">False alarm</button>
                     <button data-ack="">Acknowledge only</button>
                   </div>`}
            </div>
          </div>
        </div>
      </div>`;

    modal(`<span class="row">${priorityBadge(ev.priority, true)}
             <strong>${esc(titleise(ev.event_type))}</strong>
             <span class="mono dim small">${esc(ev.event_id)}</span></span>`,
          body,
          (root) => {
            root.querySelectorAll('[data-ack]').forEach(btn => {
              btn.addEventListener('click', async () => {
                const fb = btn.getAttribute('data-ack');
                try {
                  const res = await API.acknowledge(ev.event_id, fb || null);
                  const t = res.camera_feedback_totals || {};
                  toast(`Acknowledged${fb ? ' as ' + titleise(fb) : ''}` +
                        (fb ? ` — ${ev.camera_id} now ${t.true_positive} genuine / ${t.false_alarm} false` : ''));
                  UI.closeModal();
                  document.dispatchEvent(new CustomEvent('prahari:refresh'));
                } catch (e) { toast('Acknowledge failed: ' + e.message, 'bad'); }
              });
            });
          });
  }

  /* ==================================================================
   * Views
   * ================================================================== */

  const dashboard = {
    async render(el, state) {
      const s = state.status || {};
      const sync = s.sync || {};
      const bw = sync.bandwidth || {};
      const alerting = s.alerting || {};
      const link = UI.linkBadge(s.link_mode);

      el.innerHTML = `
        ${detectorNotice(state)}
        <div class="grid g4" style="margin-bottom:12px">
          ${statTile('Link posture', `<span style="color:var(--${s.link_mode === 'offline' ? 'bad' : s.link_mode === 'online' ? 'ok' : 'warn'})">${esc(link.label)}</span>`,
                     sync.core_reachable ? 'Sector core reachable' : 'Sector core unreachable — operating standalone')}
          ${statTile('Cameras online', `${s.cameras_online ?? 0}<span class="dim" style="font-size:14px">/${s.cameras_total ?? 0}</span>`,
                     'Analytics run locally on this node')}
          ${statTile('Alerts (1 h)', `${s.alerts_last_hour ?? 0}<span class="dim" style="font-size:14px">/${s.alert_budget_per_hour ?? 0}</span>`,
                     alerting.suppressed_last_hour
                       ? `${alerting.suppressed_last_hour} recorded without alerting — threshold ${alerting.current_threshold}`
                       : 'Within operator attention budget')}
          ${statTile('Queued for sync', `${s.events_pending_sync ?? 0}`,
                     `${bytes(s.queue_bytes)} of evidence held locally`)}
        </div>

        <div class="grid g2" style="margin-bottom:12px">
          <div class="panel">
            <div class="panel-head">
              <span class="panel-title">Active alerts</span>
              <span class="tiny dim" id="dash-alert-note"></span>
            </div>
            <div class="panel-body flush" id="dash-alerts" style="max-height:420px;overflow:auto">
              <div class="empty">Loading…</div>
            </div>
          </div>

          <div>
            <div class="panel" style="margin-bottom:12px">
              <div class="panel-head"><span class="panel-title">Sector plot</span></div>
              <div class="panel-body flush" id="dash-map"></div>
            </div>
            <div class="panel">
              <div class="panel-head"><span class="panel-title">Uplink economics</span></div>
              <div class="panel-body">
                <div class="grid g2">
                  ${statTile('Imagery processed', bytes(bw.bytes_observed), 'Encoded at this node')}
                  ${statTile('Actually transmitted', bytes(bw.bytes_transmitted),
                             `${bitrate(bw.actual_uplink_bitrate_bps)} average`)}
                </div>
                <div class="hr"></div>
                <div class="row">
                  <span class="dim small">Reduction against continuous streaming</span>
                  <span class="right mono" style="color:var(--ok);font-size:17px">
                    ${(bw.reduction_percent ?? 0).toFixed(2)}%
                  </span>
                </div>
                <p class="tiny faint" style="margin-bottom:0">${esc(bw.basis || '')}</p>
              </div>
            </div>
          </div>
        </div>

        <div class="panel">
          <div class="panel-head">
            <span class="panel-title">Cameras</span>
            <span class="tiny dim">Each camera runs only the analytics its measured capability certificate grants</span>
          </div>
          <div class="panel-body">
            <div class="grid g3" id="dash-cams"></div>
          </div>
        </div>`;

      this.paint(state);
    },

    paint(state) {
      const cams = state.cameras || [];
      const mapEl = document.getElementById('dash-map');
      if (mapEl) {
        mapEl.innerHTML = sectorMap(cams, state.selectedCamera);
        mapEl.querySelectorAll('.map-cam').forEach(g => {
          g.addEventListener('click', () => App.go('cameras'));
        });
      }
      const camEl = document.getElementById('dash-cams');
      if (camEl) camEl.innerHTML = cams.map(c => cameraCard(c, { live: true })).join('');

      const alertsEl = document.getElementById('dash-alerts');
      if (alertsEl) {
        const alerts = (state.alerts || []).filter(e => e.alerted && !e.acknowledged);
        alertsEl.innerHTML = alerts.length
          ? alerts.slice(0, 40).map(e => alertRow(e)).join('')
          : '<div class="empty">No unacknowledged alerts.</div>';
        bindAlertRows(alertsEl);
        const note = document.getElementById('dash-alert-note');
        if (note) note.textContent = `${alerts.length} unacknowledged`;
      }
    },
  };

  const cameras = {
    async render(el, state) {
      el.innerHTML = `
        ${detectorNotice(state)}
        <div class="panel">
          <div class="panel-head">
            <span class="panel-title">Live cameras</span>
            <span class="tiny dim">Detections, zones and tripwires are drawn by the node, not the browser</span>
          </div>
          <div class="panel-body">
            <div class="grid g2" id="live-cams"></div>
          </div>
        </div>`;
      this.paint(state);
    },
    paint(state) {
      const el = document.getElementById('live-cams');
      if (!el) return;
      // Rebuild only if the camera set changed; replacing the <img> restarts
      // every MJPEG stream and makes the whole wall flicker.
      const ids = (state.cameras || []).map(c => c.camera_id).join(',');
      if (el.dataset.ids !== ids) {
        el.dataset.ids = ids;
        el.innerHTML = (state.cameras || []).map(c => cameraCard(c, { live: true })).join('');
        return;
      }
      (state.cameras || []).forEach(c => {
        const card = el.querySelector(`[data-camera="${CSS.escape(c.camera_id)}"]`);
        if (!card) return;
        const fresh = h(cameraCard(c, { live: true }));
        card.querySelector('.cam-overlay').innerHTML = fresh.querySelector('.cam-overlay').innerHTML;
        card.querySelector('.cam-meta').innerHTML = fresh.querySelector('.cam-meta').innerHTML;
      });
    },
  };

  const alerts = {
    async render(el, state) {
      el.innerHTML = `
        <div class="row wrap" style="margin-bottom:12px">
          <label style="margin:0" class="row">
            <input type="checkbox" id="alerts-show-suppressed" style="width:auto;margin-right:6px">
            <span class="small dim">Include events recorded without alerting</span>
          </label>
          <span class="right tiny dim" id="alerts-summary"></span>
        </div>
        <div class="panel">
          <div class="panel-head"><span class="panel-title">Alerts, most significant first</span></div>
          <div class="panel-body flush" id="alerts-list"></div>
        </div>`;
      document.getElementById('alerts-show-suppressed')
        .addEventListener('change', () => this.paint(state));
      this.paint(state);
    },
    paint(state) {
      const el = document.getElementById('alerts-list');
      if (!el) return;
      const showSuppressed = document.getElementById('alerts-show-suppressed')?.checked;
      let list = (state.alerts || []).slice();
      if (!showSuppressed) list = list.filter(e => e.alerted);
      list.sort((a, b) => (b.priority_score - a.priority_score));
      el.innerHTML = list.length
        ? list.slice(0, 200).map(e => alertRow(e)).join('')
        : '<div class="empty">Nothing to show.</div>';
      bindAlertRows(el);
      const sum = document.getElementById('alerts-summary');
      if (sum) {
        const a = (state.status && state.status.alerting) || {};
        sum.textContent = `threshold ${a.current_threshold ?? '—'} · ` +
          `${a.alerts_last_hour ?? 0}/${a.budget_per_hour ?? 0} alerts this hour · ` +
          `${a.suppressed_last_hour ?? 0} suppressed`;
      }
    },
  };

  const events = {
    async render(el, state) {
      el.innerHTML = `
        <div class="panel">
          <div class="panel-head">
            <span class="panel-title">Event log</span>
            <span class="tiny dim">Every event is recorded and sealed, whether or not it raised an alert</span>
          </div>
          <div class="panel-body flush">
            <div class="table-scroll">
              <table>
                <thead><tr>
                  <th>Time</th><th>Priority</th><th>Type</th><th>Camera</th>
                  <th>Object</th><th>Score</th><th>Alerted</th><th>Sync</th><th>Seal</th>
                </tr></thead>
                <tbody id="events-body"></tbody>
              </table>
            </div>
          </div>
        </div>`;
      this.paint(state);
    },
    paint(state) {
      const body = document.getElementById('events-body');
      if (!body) return;
      const rows = (state.alerts || []).slice(0, 300);
      body.innerHTML = rows.length ? rows.map(e => `
        <tr data-event="${esc(e.event_id)}" style="cursor:pointer">
          <td class="mono nowrap">${timeOf(e.timestamp)}</td>
          <td>${priorityBadge(e.priority)}</td>
          <td>${esc(titleise(e.event_type))}</td>
          <td class="mono">${esc(e.camera_id)}</td>
          <td>${esc(e.object_class)}${e.track_id != null ? ' <span class="dim">#' + esc(e.track_id) + '</span>' : ''}</td>
          <td class="mono">${Number(e.priority_score).toFixed(2)}</td>
          <td>${e.alerted ? '<span class="badge b-ok tiny">YES</span>' : '<span class="badge b-neutral tiny">NO</span>'}</td>
          <td><span class="badge ${e.sync_state === 'synced' ? 'b-ok' : e.sync_state === 'pending' ? 'b-warn' : 'b-neutral'} tiny">
                ${esc(e.sync_state.toUpperCase())}</span></td>
          <td class="mono tiny">${e.ledger_index ? '#' + e.ledger_index : '—'}</td>
        </tr>`).join('')
        : '<tr><td colspan="9" class="empty">No events recorded yet.</td></tr>';
      body.querySelectorAll('tr[data-event]').forEach(tr =>
        tr.addEventListener('click', () => openEvent(tr.getAttribute('data-event'))));
    },
  };

  const capability = {
    async render(el, state) {
      el.innerHTML = `
        <div class="notice" style="margin-bottom:12px">
          Capability certificates are issued from <strong>measurements</strong>, not
          from datasheets. Each camera self-calibrates its ground plane from
          observed pedestrians, and analytics are granted per image region against
          the IEC 62676-4 pixels-on-target bands. Every refusal states its reason.
        </div>
        <div id="cap-list"></div>`;
      const list = document.getElementById('cap-list');
      list.innerHTML = '<div class="empty">Loading certificates…</div>';
      const cards = [];
      for (const cam of state.cameras || []) {
        let cert = null;
        try { cert = await API.certificate(cam.camera_id); } catch (_) { /* not yet issued */ }
        cards.push(this.card(cam, cert));
      }
      list.innerHTML = cards.join('') || '<div class="empty">No cameras.</div>';
      list.querySelectorAll('[data-reprofile]').forEach(btn =>
        btn.addEventListener('click', async () => {
          const id = btn.getAttribute('data-reprofile');
          try {
            const r = await API.reprofile(id);
            toast(`${id}: ${r.note}`);
          } catch (e) { toast('Re-profile failed: ' + e.message, 'bad'); }
        }));
    },

    card(cam, cert) {
      if (!cert) {
        return `
          <div class="panel" style="margin-bottom:12px">
            <div class="panel-head">
              <span class="panel-title">${esc(cam.camera_id)} — ${esc(cam.name)}</span>
              <span class="badge b-accent tiny">PROFILING</span>
            </div>
            <div class="panel-body">
              <p class="small dim" style="margin:0">
                No certificate issued yet. The camera is measuring its optics and
                waiting to observe enough pedestrians to fit its ground plane.
                Until then it runs person detection only.
              </p>
            </div>
          </div>`;
      }
      const m = cert.measurement;
      const granted = cert.grants.filter(g => g.granted);
      const refused = cert.grants.filter(g => !g.granted);

      return `
        <div class="panel" style="margin-bottom:12px">
          <div class="panel-head">
            <span class="panel-title">${esc(cam.camera_id)} — ${esc(cam.name)}</span>
            <div class="row">
              <span class="badge b-neutral tiny">v${cert.version}</span>
              <span class="badge b-neutral tiny">DORI ${esc(cert.overall_dori.toUpperCase())}</span>
              <span class="badge ${cert.digest_valid ? 'b-ok' : 'b-bad'} tiny">
                ${cert.digest_valid ? 'DIGEST VALID' : 'DIGEST INVALID'}</span>
              <button class="sm" data-reprofile="${esc(cam.camera_id)}">Re-profile</button>
            </div>
          </div>
          <div class="panel-body">
            <div class="grid g2">
              <div>
                <div class="stat-label">Measured</div>
                <dl class="kv small">
                  <dt>Frame</dt><dd>${m.width}×${m.height}</dd>
                  <dt>Delivered rate</dt><dd>${m.measured_fps} fps <span class="dim">(claimed ${m.claimed_fps})</span></dd>
                  <dt>Effective resolution</dt><dd>${m.effective_resolution_factor}</dd>
                  <dt>Noise floor σ</dt><dd>${m.noise_sigma}</dd>
                  <dt>Block artifacts</dt><dd>${m.compression_artifact_score}</dd>
                  <dt>Mean luma</dt><dd>${m.mean_luma}${m.is_low_light ? ' <span style="color:var(--warn)">low light</span>' : ''}</dd>
                </dl>
                <div class="stat-label" style="margin-top:10px">Self-calibrated geometry</div>
                ${m.ground_plane_estimated ? `
                  <dl class="kv small">
                    <dt>Horizon row</dt><dd>${m.horizon_y}</dd>
                    <dt>Scale, near field</dt><dd>${m.px_per_metre_near} px/m</dd>
                    <dt>Scale, far field</dt><dd>${m.px_per_metre_far} px/m</dd>
                  </dl>`
                  : '<p class="small" style="color:var(--warn)">Ground plane not recovered — metric analytics unavailable.</p>'}
              </div>
              <div>
                <div class="stat-label">Granted (${granted.length})</div>
                ${granted.map(g => `
                  <div style="margin-bottom:7px">
                    <span class="cap granted">${esc(g.capability.replace(/_/g, ' '))}</span>
                    <div class="tiny faint">${esc(g.reason)}</div>
                  </div>`).join('') || '<p class="small faint">None.</p>'}

                <div class="stat-label" style="margin-top:12px">Refused (${refused.length})</div>
                ${refused.map(g => `
                  <div style="margin-bottom:7px">
                    <span class="cap refused">${esc(g.capability.replace(/_/g, ' '))}</span>
                    <div class="tiny faint">${esc(g.reason)}</div>
                  </div>`).join('')}
              </div>
            </div>
            ${m.notes && m.notes.length ? `
              <div class="hr"></div>
              <div class="stat-label">Measurement notes</div>
              <ul class="small dim" style="margin:6px 0 0;padding-left:18px">
                ${m.notes.map(n => `<li>${esc(n)}</li>`).join('')}
              </ul>` : ''}
          </div>
        </div>`;
    },
  };

  const zones = {
    async render(el, state) {
      const all = await API.zones();
      const byCam = {};
      (all.zones || []).forEach(z => { (byCam[z.camera_id] ||= []).push(z); });

      el.innerHTML = `
        <div class="notice" style="margin-bottom:12px">
          Two rule doctrines ship. <strong>Fenced</strong> cameras run tripwire and
          restricted-zone rules, where crossing the line is itself the event.
          <strong>Open-border</strong> cameras run pattern-of-life rules against a
          lawful route, because on a treaty-open border lawful crossing is constant
          and a tripwire there fires thousands of times a day.
        </div>
        ${(state.cameras || []).map(cam => {
          const zs = byCam[cam.camera_id] || [];
          return `
            <div class="panel" style="margin-bottom:12px">
              <div class="panel-head">
                <span class="panel-title">${esc(cam.camera_id)} — ${esc(cam.name)}</span>
                <span class="badge ${cam.border_profile === 'open' ? 'b-accent' : 'b-neutral'} tiny">
                  ${esc(cam.border_profile === 'open' ? 'OPEN BORDER DOCTRINE' : 'FENCED DOCTRINE')}</span>
              </div>
              <div class="panel-body flush">
                <table>
                  <thead><tr><th>Zone</th><th>Kind</th><th>Role</th><th>Direction</th><th>Points</th></tr></thead>
                  <tbody>
                    ${zs.length ? zs.map(z => `
                      <tr>
                        <td>${esc(z.name)}<div class="tiny faint mono">${esc(z.zone_id)}</div></td>
                        <td><span class="badge b-neutral tiny">${esc(z.kind.toUpperCase())}</span></td>
                        <td>${z.is_lawful_route ? '<span class="badge b-ok tiny">LAWFUL ROUTE</span>' : '<span class="dim">restricted</span>'}</td>
                        <td class="mono">${z.direction_deg != null ? z.direction_deg + '° ±' + z.direction_tolerance_deg + '°' : '—'}</td>
                        <td class="mono">${z.points.length}</td>
                      </tr>`).join('')
                      : '<tr><td colspan="5" class="empty">No zones configured.</td></tr>'}
                  </tbody>
                </table>
              </div>
            </div>`;
        }).join('')}`;
    },
  };

  const health = {
    async render(el, state) {
      const s = state.status || {};
      const sync = s.sync || {};
      const det = s.detector || {};
      el.innerHTML = `
        <div class="grid g4" style="margin-bottom:12px">
          ${statTile('Uptime', duration(s.uptime_seconds), esc(s.node_name || ''))}
          ${statTile('Disk free', bytes(s.disk_free_bytes), 'Evidence store and queue')}
          ${statTile('Events recorded', s.events_total ?? 0, `${s.events_pending_sync ?? 0} awaiting sync`)}
          ${statTile('Inference device', esc((det.device || 'cpu').toUpperCase()),
                     esc(det.name || ''), det.simulated ? 'var(--warn)' : 'var(--ok)')}
        </div>

        <div class="grid g2">
          <div class="panel">
            <div class="panel-head"><span class="panel-title">Cameras</span></div>
            <div class="panel-body flush">
              <table>
                <thead><tr><th>Camera</th><th>State</th><th>Health</th><th>FPS</th><th>Tracks</th><th>Source</th></tr></thead>
                <tbody>
                  ${(s.cameras || []).map(c => `
                    <tr>
                      <td class="mono">${esc(c.camera_id)}</td>
                      <td>${esc(titleise(c.state))}</td>
                      <td>${healthBadge(c.health)}</td>
                      <td class="mono">${c.fps} <span class="faint">/ ${c.nominal_fps}</span></td>
                      <td class="mono">${c.tracks}</td>
                      <td class="tiny dim">${esc(c.source && c.source.simulated ? 'simulated' : (c.source && c.source.url) || 'stream')}</td>
                    </tr>`).join('')}
                </tbody>
              </table>
            </div>
          </div>

          <div class="panel">
            <div class="panel-head"><span class="panel-title">Uplink and queue</span></div>
            <div class="panel-body">
              <dl class="kv small">
                <dt>Link posture</dt><dd>${esc(s.link_mode)}</dd>
                <dt>Core reachable</dt><dd>${sync.core_reachable ? 'yes' : 'no'}</dd>
                <dt>Core URL</dt><dd>${esc(sync.core_url || '')}</dd>
                <dt>Pending</dt><dd>${sync.pending ?? 0}</dd>
                <dt>Synced</dt><dd>${sync.synced ?? 0}</dd>
                <dt>Failed</dt><dd>${sync.failed ?? 0}</dd>
                <dt>Evidence evicted</dt><dd>${sync.evicted ?? 0}</dd>
                <dt>Queue pressure</dt><dd>${((sync.queue_pressure ?? 0) * 100).toFixed(1)}%</dd>
                <dt>Last sync</dt><dd>${sync.last_sync_at ? ago(sync.last_sync_at) : 'never'}</dd>
                <dt>Clock trusted</dt><dd>${sync.clock_trusted ? 'yes' : 'no'}</dd>
              </dl>
              ${sync.last_error ? `<div class="notice bad" style="margin-top:10px">${esc(sync.last_error)}</div>` : ''}
            </div>
          </div>
        </div>`;
    },
  };

  const integrity = {
    async render(el, state) {
      el.innerHTML = `
        <div class="notice" style="margin-bottom:12px">
          Every event is appended to a hash chain: each entry commits to the hash
          of the one before it, so altering or removing any event after the fact
          breaks every link that follows. This is what lets a sector core accept a
          three-day backlog from a disconnected outpost and still check it.
          <br><br>
          <strong>This is tamper-evident, not tamper-proof.</strong> Anyone holding
          this node's key material could forge a consistent chain. Hardware-backed
          keys and countersigning at the core are designed for and not yet built.
        </div>
        <div class="panel" style="margin-bottom:12px">
          <div class="panel-head">
            <span class="panel-title">Chain verification</span>
            <button class="sm primary" id="verify-btn">Verify now</button>
          </div>
          <div class="panel-body" id="verify-result">
            <p class="small dim" style="margin:0">Press verify to walk the whole chain.</p>
          </div>
        </div>
        <div class="panel">
          <div class="panel-head"><span class="panel-title">Audit log</span></div>
          <div class="panel-body flush"><div class="table-scroll" id="audit-body"></div></div>
        </div>`;

      document.getElementById('verify-btn').addEventListener('click', async () => {
        const out = document.getElementById('verify-result');
        out.innerHTML = '<p class="small dim">Verifying…</p>';
        try {
          const r = await API.verifyChain();
          out.innerHTML = `
            <div class="row">
              <span class="badge ${r.valid ? 'b-ok' : 'b-bad'} solid">${r.valid ? 'CHAIN INTACT' : 'CHAIN BROKEN'}</span>
              <span class="small dim">${esc(r.message || '')}</span>
            </div>
            ${r.head ? `<div class="tiny faint mono" style="margin-top:8px">head ${esc(r.head)}</div>` : ''}`;
        } catch (e) {
          out.innerHTML = `<div class="notice bad">${esc(e.message)}</div>`;
        }
      });

      try {
        const a = await API.audit(120);
        document.getElementById('audit-body').innerHTML = `
          <table>
            <thead><tr><th>Time</th><th>Actor</th><th>Action</th><th>Target</th><th>Detail</th></tr></thead>
            <tbody>${(a.entries || []).map(e => `
              <tr>
                <td class="mono nowrap tiny">${dateTimeOf(e.ts)}</td>
                <td class="mono">${esc(e.actor)}</td>
                <td>${esc(e.action)}</td>
                <td class="mono tiny">${esc(e.target)}</td>
                <td class="tiny dim">${esc(String(e.detail).slice(0, 140))}</td>
              </tr>`).join('')}</tbody>
          </table>`;
      } catch (e) {
        document.getElementById('audit-body').innerHTML =
          `<div class="empty">Audit log requires the administrator role.</div>`;
      }
    },
  };

  const demo = {
    async render(el, state) {
      el.innerHTML = `
        <div class="notice warn" style="margin-bottom:12px">
          Every control here injects something into the <strong>simulated scene</strong>
          and then leaves the real pipeline to find it. Nothing fabricates an event:
          an injected intruder must be detected, tracked and ruled on exactly like
          anything else, which is what makes this a demonstration rather than a
          slideshow.
        </div>

        <div class="grid g2">
          <div class="panel">
            <div class="panel-head"><span class="panel-title">Scene injection</span></div>
            <div class="panel-body">
              <div class="field">
                <label for="demo-cam">Camera</label>
                <select id="demo-cam">
                  ${(state.cameras || []).map(c =>
                    `<option value="${esc(c.camera_id)}">${esc(c.camera_id)} — ${esc(c.name)}</option>`).join('')}
                </select>
              </div>
              <div class="row wrap">
                <button class="primary" data-demo="intrusion">Approach &amp; cross the line</button>
                <button data-demo="loiter">Loiterer</button>
                <button data-demo="group">Group of four</button>
                <button data-demo="vehicle">Vehicle with plate</button>
              </div>
              <div class="hr"></div>
              <div class="stat-label">Friendly force</div>
              <div class="row wrap" style="margin-top:6px">
                <button data-demo="patrol">Patrol on its route</button>
                <button data-demo="patrol_reverse">Patrol walking the wrong way</button>
              </div>
              <p class="tiny faint" style="margin:6px 0 0">
                Both inject the same two-man foot patrol; only the direction
                differs. Whether the result is suppressed or escalated is
                decided by the matcher from the real track, not by the button.
              </p>
              <div class="hr"></div>
              <div class="stat-label">Sensor attack</div>
              <div class="row wrap" style="margin-top:6px">
                <button data-tamper="covered">Cover the lens</button>
                <button data-tamper="blinded">Blind with light</button>
                <button data-tamper="frozen">Replay a frozen feed</button>
                <button data-tamper="clear">Clear</button>
              </div>
            </div>
          </div>

          <div class="panel">
            <div class="panel-head"><span class="panel-title">Connectivity</span></div>
            <div class="panel-body">
              <p class="small dim" style="margin-top:0">
                Taking the link down does not stop analytics. Detection continues at
                the edge, events queue locally with their evidence, and the backlog
                drains automatically when the link returns — with original
                timestamps preserved.
              </p>
              <div class="row wrap">
                <button class="danger" data-demo="network_outage">Cut the uplink</button>
                <button class="good" data-demo="network_restore">Restore the uplink</button>
              </div>
              <div class="hr"></div>
              <div id="demo-sync"></div>
            </div>
          </div>
        </div>`;

      const camOf = () => document.getElementById('demo-cam').value;

      el.querySelectorAll('[data-demo]').forEach(btn =>
        btn.addEventListener('click', async () => {
          const action = btn.getAttribute('data-demo');
          btn.disabled = true;
          try {
            const extra = action.startsWith('network') ? {} : { camera_id: camOf() };
            if (action === 'group') extra.size = 4;
            if (action === 'vehicle') extra.plate = 'DEMO 0000';
            const r = await API.demo(action, extra);
            toast(r.note || `${titleise(action)} — done`);
          } catch (e) { toast('Failed: ' + e.message, 'bad'); }
          finally { btn.disabled = false; }
        }));

      el.querySelectorAll('[data-tamper]').forEach(btn =>
        btn.addEventListener('click', async () => {
          try {
            const r = await API.demo('tamper', { camera_id: camOf(), mode: btn.getAttribute('data-tamper') });
            toast(`Camera integrity: ${r.mode}`);
          } catch (e) { toast('Failed: ' + e.message, 'bad'); }
        }));

      this.paint(state);
    },
    paint(state) {
      const el = document.getElementById('demo-sync');
      if (!el) return;
      const sync = (state.status && state.status.sync) || {};
      el.innerHTML = `
        <dl class="kv small">
          <dt>Link posture</dt><dd>${esc(sync.link_mode || '—')}</dd>
          <dt>Core reachable</dt><dd>${sync.core_reachable ? 'yes' : 'no'}</dd>
          <dt>Queued locally</dt><dd>${sync.pending ?? 0}</dd>
          <dt>Synchronised</dt><dd>${sync.synced ?? 0}</dd>
        </dl>`;
    },
  };


  const crosscam = {
    async render(el, state) {
      el.innerHTML = `
        <div class="notice" style="margin-bottom:12px">
          A single camera raises its own events. The corridor is what a sector
          actually is: the coordinator links a track leaving one camera to the
          track arriving at the next — on the learnt transition time, the object
          class and a coarse appearance signature — and gives it one identity
          across the sector. An object that entered the corridor and never
          reached the far end did not leave frame; it left the corridor.
        </div>
        <div class="grid g4" style="margin-bottom:12px" id="cc-stats"></div>
        <div class="grid g2">
          <div class="panel">
            <div class="panel-head"><span class="panel-title">Recent handoffs</span></div>
            <div class="panel-body flush" id="cc-handoffs"></div>
          </div>
          <div class="panel">
            <div class="panel-head"><span class="panel-title">Corridor topology</span>
              <span class="tiny dim">transition times are learned from confirmed handoffs</span>
            </div>
            <div class="panel-body flush" id="cc-topology"></div>
          </div>
        </div>
        <div class="panel" style="margin-top:12px">
          <div class="panel-head"><span class="panel-title">Entities seen on more than one camera</span></div>
          <div class="panel-body flush" id="cc-entities"></div>
        </div>`;
      await this.load();
    },

    async load() {
      let d;
      try { d = await API.crosscam(); }
      catch (e) {
        const s = document.getElementById('cc-stats');
        if (s) s.innerHTML = `<div class="notice bad">${esc(e.message)}</div>`;
        return;
      }
      this._data = d;

      const stats = document.getElementById('cc-stats');
      if (stats) stats.innerHTML = [
        statTile('Global entities', d.entities_total,
                 'distinct objects the sector has identified'),
        statTile('Seen on 2+ cameras', d.multi_camera_entities,
                 'linked by a confirmed handoff'),
        statTile('Open handoffs', d.open_handoffs,
                 'objects in transit between cameras'),
        statTile('Corridor edges', (d.topology?.edges || []).length,
                 'directed camera adjacencies'),
      ].join('');

      const hs = (d.recent_handoffs || []).slice().reverse();
      const hEl = document.getElementById('cc-handoffs');
      if (hEl) hEl.innerHTML = hs.length ? `
        <table><thead><tr><th>From</th><th>To</th><th>Object</th>
        <th>Transit</th><th>Score</th><th>Global</th></tr></thead><tbody>
        ${hs.slice(0, 25).map(h => `
          <tr>
            <td class="mono">${esc(h.from_camera)}</td>
            <td class="mono">${esc(h.to_camera)}</td>
            <td>${esc(h.object_class)}</td>
            <td class="mono">${esc(h.transition_s)}s</td>
            <td class="mono">${esc(h.score)}</td>
            <td class="mono">#${esc(h.global_id)}</td>
          </tr>`).join('')}
        </tbody></table>`
        : '<div class="empty">No handoffs yet. Trigger a corridor journey from the Demonstration tab.</div>';

      const tEl = document.getElementById('cc-topology');
      const edges = d.topology?.edges || [];
      if (tEl) tEl.innerHTML = edges.length ? `
        <table><thead><tr><th>Edge</th><th>Mean transit</th><th>Spread</th>
        <th>Observed</th><th>Arrival rate</th></tr></thead><tbody>
        ${edges.map(e => `
          <tr>
            <td class="mono">${esc(e.src)} &rarr; ${esc(e.dst)}</td>
            <td class="mono">${esc(e.mean_transition_s)}s</td>
            <td class="mono">±${esc(e.std_transition_s)}s</td>
            <td class="mono">${esc(e.observations)}</td>
            <td class="mono">${e.observations ? (e.arrival_rate*100).toFixed(0)+'%' : '—'}</td>
          </tr>`).join('')}
        </tbody></table>` : '<div class="empty">No topology configured.</div>';

      const multi = (d.entities || []).filter(e => (e.cameras || []).length > 1);
      const eEl = document.getElementById('cc-entities');
      if (eEl) eEl.innerHTML = multi.length ? `
        <table><thead><tr><th>Global</th><th>Object</th><th>Cameras</th>
        <th>Handoffs</th><th>First seen</th><th>Last seen</th></tr></thead><tbody>
        ${multi.slice(0, 40).map(e => `
          <tr>
            <td class="mono">#${esc(e.global_id)}</td>
            <td>${esc(e.object_class)}</td>
            <td class="mono">${esc((e.cameras || []).join(' → '))}</td>
            <td class="mono">${esc(e.handoffs)}</td>
            <td class="mono tiny">${timeOf(e.first_seen)}</td>
            <td class="mono tiny">${timeOf(e.last_seen)}</td>
          </tr>`).join('')}
        </tbody></table>`
        : '<div class="empty">No object has yet been linked across two cameras.</div>';
    },

    paint() { /* refreshed on demand; the sector view is not per-frame */ },
  };

  const patrol = {
    async render(el, state) {
      el.innerHTML = `
        <div class="notice" style="margin-bottom:12px">
          Own patrols walk the same routes every night and trip every rule on
          them — routinely the single largest source of false alarms on a fenced
          sector, arriving at the same hour, which is exactly the pattern that
          teaches an operator to stop looking. This layer holds movement against
          a <strong>declared</strong> patrol: right camera, right zone, right
          window, right direction, plausible pace. All of it, or no suppression.
          Matching a patrol's identity while breaking its expectations
          <strong>raises</strong> the priority instead.
        </div>

        <div class="grid g4" style="margin-bottom:12px" id="pt-stats"></div>

        <div class="panel" style="margin-bottom:12px">
          <div class="panel-head">
            <span class="panel-title">Decision policy, evaluated live</span>
            <span class="tiny dim">the production matcher, run over five fixed observations</span>
          </div>
          <div class="panel-body flush" id="pt-scenarios"></div>
        </div>

        <div class="panel" style="margin-bottom:12px">
          <div class="panel-head"><span class="panel-title">Declared patrol roster</span>
            <span class="tiny dim">configuration, not learnt state</span>
          </div>
          <div class="panel-body flush" id="pt-roster"></div>
        </div>

        <div class="panel">
          <div class="panel-head"><span class="panel-title">Events a patrol accounted for</span></div>
          <div class="panel-body flush" id="pt-events"></div>
        </div>`;
      await this.load();
    },

    async load() {
      let roster, scen, events;
      try {
        [roster, scen, events] = await Promise.all([
          API.patrols(), API.patrolScenarios(), API.events({ limit: 300 }),
        ]);
      } catch (e) {
        const s = document.getElementById('pt-stats');
        if (s) s.innerHTML = `<div class="notice bad">${esc(e.message)}</div>`;
        return;
      }

      const m = roster.metrics || {};
      const stats = document.getElementById('pt-stats');
      if (stats) stats.innerHTML = [
        statTile('Events assessed', m.raw_events ?? 0,
                 'every event is assessed and recorded'),
        statTile('Patrol matched', m.patrol_matched_events ?? 0,
                 `${m.suppressed_events ?? 0} suppressed, ${m.downgraded_events ?? 0} downgraded`),
        statTile('Patrol deviations', m.abnormal_patrol_events ?? 0,
                 'escalated, not suppressed',
                 (m.abnormal_patrol_events ? 'var(--high)' : null)),
        statTile('Removed from the alert path',
                 (m.alert_reduction_percent ?? 0).toFixed(1) + '%',
                 'share of assessed events this run'),
      ].join('');

      const sEl = document.getElementById('pt-scenarios');
      if (sEl) sEl.innerHTML = `
        <table><thead><tr><th></th><th>Scenario</th><th>Observed</th>
        <th>Expected</th><th>Decided</th><th>Match</th></tr></thead><tbody>
        ${(scen.scenarios || []).map(r => `
          <tr>
            <td><span class="badge ${r.passed ? 'b-ok' : 'b-high'} tiny">${r.key}</span></td>
            <td>${esc(r.title)}<div class="tiny faint">${esc(r.narrative)}</div></td>
            <td class="mono tiny">${esc(r.camera_id)}<br>${esc(r.observed_time)} · ${Number(r.observed_heading_deg).toFixed(0)}°</td>
            <td class="tiny">${esc(titleise(r.expected_decision))}</td>
            <td>${esc(titleise(r.actual_decision))}
                <div class="tiny faint">${esc(r.expected_effect)}</div></td>
            <td class="mono">${Number(r.match_score).toFixed(2)}</td>
          </tr>`).join('')}
        </tbody></table>
        <div class="panel-body">
          <p class="tiny faint" style="margin:0">${esc(scen.note || '')}</p>
        </div>`;

      const rEl = document.getElementById('pt-roster');
      const profiles = roster.profiles || [];
      if (rEl) rEl.innerHTML = profiles.length ? `
        <table><thead><tr><th>Patrol</th><th>Route</th><th>Window</th>
        <th>Direction</th><th>Budget</th><th>Status</th></tr></thead><tbody>
        ${profiles.map(p => `
          <tr>
            <td><span class="mono">${esc(p.patrol_id)}</span>
                <div class="tiny faint">${esc(p.name)}</div></td>
            <td class="mono tiny">${esc((p.cameras || []).join(' → '))}</td>
            <td class="mono tiny">${esc((p.windows || []).map(w => windowLabel(w)).join('; ') || '—')}</td>
            <td class="mono tiny">${p.expected_heading_deg != null
                ? Number(p.expected_heading_deg).toFixed(0) + '° ±' + Number(p.heading_tolerance_deg).toFixed(0)
                : '—'}</td>
            <td class="mono tiny">${esc(p.max_suppressions_per_window)}/window</td>
            <td><span class="badge ${p.active ? 'b-ok' : 'b-neutral'} tiny">${p.active ? 'ACTIVE' : 'INACTIVE'}</span></td>
          </tr>`).join('')}
        </tbody></table>`
        : '<div class="empty">No patrol profiles declared. Without one, every event takes the normal alert path.</div>';

      const matched = (events || []).filter(e => e.patrol && e.patrol.decision !== 'not_matched');
      const eEl = document.getElementById('pt-events');
      if (eEl) eEl.innerHTML = matched.length ? `
        <table><thead><tr><th>Time</th><th>Camera</th><th>Event</th>
        <th>Decision</th><th>Match</th><th>Alerted</th></tr></thead><tbody>
        ${matched.slice(0, 40).map(e => `
          <tr data-event="${esc(e.event_id)}" style="cursor:pointer">
            <td class="mono tiny">${timeOf(e.timestamp)}</td>
            <td class="mono">${esc(e.camera_id)}</td>
            <td>${esc(titleise(e.event_type))}
                <div class="tiny faint">${esc(e.patrol.patrol_name || '')}</div></td>
            <td>${patrolBadge(e.patrol, true)}</td>
            <td class="mono">${Number(e.patrol.match_score).toFixed(2)}</td>
            <td class="tiny">${e.alerted ? 'yes' : 'recorded only'}</td>
          </tr>`).join('')}
        </tbody></table>`
        : `<div class="empty">No event has been matched to a patrol yet.
             Run <strong>Demonstration → Patrol on its route</strong> on a camera
             covered by an active profile.</div>`;
      if (eEl) bindAlertRows(eEl);
    },

    paint() { /* refreshed on demand; not a per-frame view */ },
  };

  function windowLabel(w) {
    const hhmm = (m) => String(Math.floor(m / 60)).padStart(2, '0') + ':' +
                        String(m % 60).padStart(2, '0');
    const days = (w.days && w.days.length)
      ? w.days.map(d => ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][d]).join(' ')
      : 'daily';
    return `${hhmm(w.start_minute)}-${hhmm(w.end_minute)} ${days} ±${w.tolerance_minutes}m`;
  }

  /* --- helpers ---------------------------------------------------------- */
  function bindAlertRows(root) {
    root.querySelectorAll('[data-event]').forEach(node =>
      node.addEventListener('click', () => openEvent(node.getAttribute('data-event'))));
  }

  return { dashboard, cameras, alerts, events, crosscam, patrol, capability, zones, health, integrity, demo, openEvent };
})();

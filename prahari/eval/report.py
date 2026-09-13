"""Render an evaluation run as JSON and as a readable HTML report.

The HTML is intentionally self-contained and dependency-free - it has to open on
any machine, including one that has never seen the internet, which is the same
constraint the dashboard runs under. It is a report, not an app: no build step,
no fonts to fetch, one file you can email.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _pct(v: float | None) -> str:
    return f"{v:.1f}%" if v is not None else "—"


def render_json(results: list[dict[str, Any]], meta: dict[str, Any]) -> str:
    return json.dumps({"meta": meta, "scenarios": results}, indent=2)


def _verdict_row(r: dict[str, Any]) -> str:
    ev = r["events"]
    det = r["detection"]
    prof = r["profiling"]
    perf = r["performance"]

    intruder = ("YES" if ev["intruder_detected"] else "NO")
    intruder_cls = "ok" if ev["intruder_detected"] else "bad"
    latency = (f"{ev['detection_latency_s']}s"
               if ev["detection_latency_s"] is not None else "—")
    height_err = prof["height_error_percent"]
    height_cls = ("ok" if (height_err is not None and height_err <= 5)
                  else "warn" if (height_err is not None and height_err <= 15)
                  else "bad" if height_err is not None else "muted")

    supp = int(ev['alert_suppression_rate'] * 100)
    supp_cls = "ok" if supp >= 85 else "warn" if supp >= 60 else "bad"
    return f"""
    <tr>
      <td class="mono">{r['camera_id']}</td>
      <td class="{intruder_cls}">{intruder}</td>
      <td>{latency}</td>
      <td class="{supp_cls}">{supp}%</td>
      <td>{ev['raw_false_alarm_events']} &rarr; {ev['alerted_false_alarms']}</td>
      <td>{det['recall']:.2f}</td>
      <td>{det['precision']:.2f}</td>
      <td class="{height_cls}">{_pct(height_err)}</td>
      <td>{perf['throughput_fps']:.0f}</td>
    </tr>"""


def _scenario_detail(r: dict[str, Any]) -> str:
    det = r["detection"]
    rows = ""
    for cls, c in det.get("by_class", {}).items():
        rows += (f"<tr><td class='mono'>{cls}</td><td>{c['tp']}</td>"
                 f"<td>{c['fp']}</td><td>{c['fn']}</td>"
                 f"<td>{c['precision']:.2f}</td><td>{c['recall']:.2f}</td></tr>")
    notes = "".join(f"<li>{n}</li>" for n in r.get("notes", []))
    return f"""
    <div class="card">
      <h3>{r['camera_id']} <span class="muted mono">
        {r['detector']['name']}{' · SIMULATED' if r['detector']['simulated'] else ''}
      </span></h3>
      <div class="grid">
        <div><span class="lbl">Granted analytics</span>
          <div class="mono small">{', '.join(r['granted_capabilities']) or '—'}</div></div>
        <div><span class="lbl">Run</span>
          <div>{r['frames']} frames · {r['run_seconds']}s simulated ·
          {r['wall_seconds']}s wall</div></div>
      </div>
      {'<table class="mini"><thead><tr><th>class</th><th>TP</th><th>FP</th>'
       '<th>FN</th><th>prec</th><th>recall</th></tr></thead><tbody>'
       + rows + '</tbody></table>' if rows else ''}
      {'<ul class="notes">' + notes + '</ul>' if notes else ''}
    </div>"""


def render_html(results: list[dict[str, Any]], meta: dict[str, Any]) -> str:
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    verdict_rows = "".join(_verdict_row(r) for r in results)
    details = "".join(_scenario_detail(r) for r in results)

    any_sim = any(r["detector"]["simulated"] for r in results)
    caveat = ""
    if any_sim:
        caveat = """
    <div class="caveat">
      <strong>Read the detector column.</strong> Scenarios marked SIMULATED use
      Prahari's synthetic detector, which is driven by the simulator's ground
      truth. Their detection precision and recall characterise that detector's
      <em>modelled</em> miss rate and noise — they are not the accuracy of a
      trained network, and must not be quoted as such. The event-level metrics
      (intruder detected, latency, false-alarm rate, alert suppression) and the
      profiling accuracy are meaningful regardless, because they depend on the
      whole pipeline and on the simulator's real geometry and degradation. To
      produce genuine detection accuracy, run this same harness with a real model
      against real footage — see <span class="mono">docs/evaluation.md</span>.
    </div>"""

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Prahari — Evaluation Report</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:#0d1117; color:#dbe3ec;
         font-family: system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
         font-size:14px; line-height:1.5; }}
  .wrap {{ max-width: 1000px; margin: 0 auto; padding: 28px 20px 60px; }}
  h1 {{ font-size:20px; letter-spacing:.12em; margin:0 0 4px; }}
  h2 {{ font-size:12px; text-transform:uppercase; letter-spacing:.1em;
        color:#8b98a8; margin:32px 0 10px; border-bottom:1px solid #232c38;
        padding-bottom:6px; }}
  h3 {{ font-size:15px; margin:0 0 10px; }}
  .mono {{ font-family: ui-monospace,"Cascadia Mono",Menlo,Consolas,monospace; }}
  .muted, .small {{ color:#8b98a8; }}
  .small {{ font-size:12px; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th {{ text-align:left; font-size:10.5px; text-transform:uppercase;
        letter-spacing:.06em; color:#5c6875; padding:8px 9px;
        border-bottom:1px solid #232c38; }}
  td {{ padding:8px 9px; border-bottom:1px solid #1a222c; }}
  .ok {{ color:#45c98a; }} .warn {{ color:#f5c451; }} .bad {{ color:#ff4d5e; }}
  .card {{ background:#141a22; border:1px solid #232c38; border-radius:6px;
           padding:14px 16px; margin-bottom:12px; }}
  .grid {{ display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-bottom:10px; }}
  .lbl {{ font-size:10.5px; text-transform:uppercase; letter-spacing:.08em;
          color:#5c6875; }}
  table.mini {{ margin-top:8px; }}
  table.mini th {{ padding:5px 8px; }} table.mini td {{ padding:5px 8px; }}
  .notes {{ margin:10px 0 0; padding-left:18px; color:#8b98a8; font-size:12.5px; }}
  .caveat {{ background:#1c1a10; border:1px solid #443a1c; color:#e8d9a8;
             border-radius:6px; padding:12px 15px; margin:14px 0; font-size:13px; }}
  .meta {{ color:#5c6875; font-size:12px; }}
  .legend {{ color:#5c6875; font-size:11.5px; margin-top:6px; }}
</style></head>
<body><div class="wrap">
  <h1>PRAHARI — EVALUATION REPORT</h1>
  <div class="meta">Generated {generated} · {meta.get('scenario_count', len(results))}
     scenario(s) · seed {meta.get('seed', '—')}</div>
  {caveat}

  <h2>Summary</h2>
  <table>
    <thead><tr>
      <th>Camera</th><th>Intruder<br>detected</th><th>Latency</th>
      <th>Alerts<br>suppressed</th><th>False alarms<br>raw &rarr; alerted</th>
      <th>Det.<br>recall</th><th>Det.<br>prec</th>
      <th>Profiling<br>err</th><th>fps</th>
    </tr></thead>
    <tbody>{verdict_rows}</tbody>
  </table>
  <div class="legend">
    <strong>Latency</strong> = time from the intruder entering frame to the first
    attributable event. <strong>Alerts suppressed</strong> = fraction of rule
    events the governor recorded without interrupting an operator — the robust
    headline, and the one that decides whether a system survives night three.
    <strong>False alarms raw&nbsp;&rarr;&nbsp;alerted</strong> = the rule-engine
    flood, then what reached the operator after the governor. <strong>Profiling
    err</strong> = recovered vs true camera mounting height.
    <br><br>
    Note: a per-hour false-alarm rate is deliberately not quoted. Extrapolating it
    from a &lt;1-minute run is dominated by the governor's start-up transient (its
    budget is hourly), and the live node's learnt pattern of life — not applied in
    this harness — damps the rate further. The suppression fraction is
    run-length-robust; the per-hour rate is not, and inventing one would be the
    kind of number this project exists to avoid.
  </div>

  <h2>Per-scenario detail</h2>
  {details}

  <h2>How to read this</h2>
  <div class="card small">
    <p>The metric this system lives or dies on is not detection accuracy but the
    two columns beside it: whether the right alarm fired at all, and how many
    wrong ones fired alongside it. A detector that never misses is worthless if
    its operator muted the console on night three.</p>
    <p>Every number here is derived from ~40 lines of readable code in
    <span class="mono">prahari/eval/metrics.py</span>, against ground truth the
    harness controls. Nothing is a self-reported model claim.</p>
  </div>
</div></body></html>"""


def write_reports(results: list[dict[str, Any]], meta: dict[str, Any],
                  out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "evaluation.json"
    html_path = out_dir / "evaluation.html"
    json_path.write_text(render_json(results, meta), encoding="utf-8")
    html_path.write_text(render_html(results, meta), encoding="utf-8")
    return json_path, html_path

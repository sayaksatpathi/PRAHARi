"""Verify friendly-force suppression against its five reference scenarios.

Runs the production `PatrolMatcher` - not a copy of it, not a stub - over the
five fixed observations declared in `prahari.edge.patrol.scenarios`, and prints
what it actually decided next to what it was specified to decide.

    .venv/Scripts/python.exe scripts/verify_patrol.py

Exits non-zero if any scenario disagrees, so it is usable as a gate.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prahari.edge.patrol.matcher import (          # noqa: E402
    DEVIATION_ADJUSTMENT, OFF_SCHEDULE_CAP, STRONG_MATCH, SUPPRESS_ADJUSTMENT,
    UNCERTAIN_MATCH,
)
from prahari.edge.patrol.scenarios import run_scenarios, scenario_profile  # noqa: E402


def main() -> int:
    profile = scenario_profile()
    print()
    print("Prahari - friendly-force (patrol) suppression")
    print("=" * 78)
    print(f"  profile      {profile.patrol_id}  {profile.name}")
    print(f"  route        {' -> '.join(profile.cameras)}")
    print(f"  window       {profile.windows[0].describe()} "
          f"(+/-{profile.windows[0].tolerance_minutes} min tolerance)")
    print(f"  direction    {profile.expected_heading_deg:.0f} deg "
          f"+/-{profile.heading_tolerance_deg:.0f}")
    print(f"  policy       suppress >= {STRONG_MATCH:.2f}, "
          f"downgrade >= {UNCERTAIN_MATCH:.2f}, "
          f"off-schedule capped at {OFF_SCHEDULE_CAP:.2f}")
    print(f"  adjustments  suppress {SUPPRESS_ADJUSTMENT:+.2f}, "
          f"deviation {DEVIATION_ADJUSTMENT:+.2f}")
    print("=" * 78)

    results = run_scenarios()
    failures = 0
    for r in results:
        ok = r["passed"]
        failures += 0 if ok else 1
        print()
        print(f"  [{'PASS' if ok else 'FAIL'}]  {r['key']}. {r['title']}")
        print(f"          {r['narrative']}")
        print(f"          observed  {r['camera_id']} / {r['zone']} "
              f"at {r['observed_time']}, heading {r['observed_heading_deg']:.0f} deg")
        print(f"          expected  {r['expected_decision']}")
        print(f"          decided   {r['actual_decision']}   "
              f"(match {r['match_score']:.2f}, identity "
              f"{r['identity_confidence']:.2f}, conformance "
              f"{r['conformance']:.2f}, score {r['score_adjustment']:+.2f})")
        for line in _wrap(r["reason"], 68):
            print(f"          {line}")

    print()
    print("=" * 78)
    suppressed = sum(1 for r in results if r["actual_decision"] == "suppressed")
    escalated = sum(1 for r in results if r["actual_decision"] == "deviation")
    print(f"  {len(results) - failures}/{len(results)} scenarios decided as specified")
    print(f"  {suppressed} suppressed, {escalated} escalated as patrol deviations")
    print()
    print("  These are deterministic policy checks on constructed observations.")
    print("  They demonstrate what the decision logic does. They are not a")
    print("  measurement of false-alarm reduction on real border footage, which")
    print("  would require annotated ground truth this build does not have.")
    print("=" * 78)
    print()
    return 1 if failures else 0


def _wrap(text: str, width: int) -> list[str]:
    words, lines, current = text.split(), [], ""
    for word in words:
        if len(current) + len(word) + 1 > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines


if __name__ == "__main__":
    raise SystemExit(main())

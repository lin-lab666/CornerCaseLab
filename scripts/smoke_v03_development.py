"""Thin v0.3 development smoke driver: REAL HighwayEnv, tiny budget, no claims.

This is a CONNECTIVITY test, not an experiment. It exercises the v0.3 path
``compare_methods -> run_method -> MethodRunner -> simulator.evaluate`` against
the real simulator so that "does the v0.3 accounting layer actually drive
HighwayEnv?" is answered by evidence rather than by unit tests with fakes.

Explicit non-claims
-------------------
* ``purpose="development"``. The master seed is the v0.2 calibration seed, which
  is only permitted for development; this is not evaluation evidence.
* ``audit_repeats=5`` is a plumbing value chosen so the plan closes at
  total=20 (search 10 + audit 2x5). It is NOT a stable-failure criterion, no
  stability classification is computed, and nothing here is an n=30 protocol.
* Zero collisions would be a perfectly valid outcome. No parameter is tuned and
  no run is repeated to obtain collisions.

Run:  .venv\\Scripts\\python.exe scripts\\smoke_v03_development.py
"""
from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cornercaselab.experiments import (  # noqa: E402
    METHODS,
    MethodConfig,
    compare_methods,
    make_simulator_evaluator,
)

# v0.2 calibration seed; permitted only because purpose="development".
SEED = 20261003
# Frozen controller under test.
POLICY = "boundary"
TOTAL_BUDGET = 20
SEARCH_FRACTION = 0.5            # -> search_pool 10
AUDIT_CANDIDATES = 2
AUDIT_REPEATS = 5                # -> audit_pool 10; 10 + 10 == 20 closes exactly
BUDGET_SLOTS_TOTAL = TOTAL_BUDGET * len(METHODS)

SEARCH = "search"
INTERNAL_CONFIRM = "internal_confirm"
FINAL_AUDIT = "final_audit"
REAL_NOTE = "real HighwayEnv episode"


def _table(payload: dict) -> str:
    header = (f"{'method':<26}{'search':>7}{'internal':>9}{'audit':>7}"
              f"{'unspent':>8}{'remaining':>10}{'collisions':>11}"
              f"{'samples':>8}{'errors':>7}")
    lines = [header, "-" * len(header)]
    for method in payload["methods"]:
        acct = method["accounting"]
        phases = acct["phase_episodes"]
        lines.append(
            f"{method['method']:<26}{phases[SEARCH]:>7}{phases[INTERNAL_CONFIRM]:>9}"
            f"{phases[FINAL_AUDIT]:>7}"
            f"{method['audit']['audit_reserved_unspent']:>8}"
            f"{acct['episodes_remaining']:>10}"
            f"{method['search_collisions']:>11}"
            f"{acct['collisions']:>8}"
            f"{acct['errors']:>7}")
    return "\n".join(lines)


def _ledger_scan(payload: dict) -> tuple[int, list[str]]:
    """(rows produced by a real HighwayEnv episode, distinct error messages)."""
    real_rows = 0
    errors: list[str] = []
    for method in payload["methods"]:
        for row in method["ledger"]:
            outcome = row["outcome"]
            if outcome.get("note") == REAL_NOTE:
                real_rows += 1
            message = outcome.get("error")
            if message and message not in errors:
                errors.append(message)
    return real_rows, errors


def main() -> int:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = ROOT / "runs" / f"v03_smoke_dev_{stamp}"
    print(f"output_dir={out_dir}")
    print(f"seed={SEED} policy={POLICY} purpose=development "
          f"total_budget={TOTAL_BUDGET} search_fraction={SEARCH_FRACTION} "
          f"audit={AUDIT_CANDIDATES}x{AUDIT_REPEATS}")
    print(f"methods={list(METHODS)} budget_slots_total={BUDGET_SLOTS_TOTAL}")
    sys.stdout.flush()

    try:
        payload = compare_methods(
            methods=list(METHODS), total_budget=TOTAL_BUDGET, seed=SEED, policy=POLICY,
            search_fraction=SEARCH_FRACTION, audit_candidates=AUDIT_CANDIDATES,
            audit_repeats=AUDIT_REPEATS, config=MethodConfig(),
            evaluator=make_simulator_evaluator(), purpose="development", out=out_dir)
    except BaseException:
        out_dir.mkdir(parents=True, exist_ok=True)
        detail = traceback.format_exc()
        (out_dir / "smoke_error.json").write_text(
            json.dumps({"status": "blocked", "output_dir": str(out_dir),
                        "traceback": detail}, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        print("\nBLOCKED: the v0.3 smoke run did not complete.")
        print(detail)
        return 1

    table = _table(payload)
    real_rows, errors = _ledger_scan(payload)
    launched = sum(m["accounting"]["episodes_spent"] for m in payload["methods"])
    error_rows = sum(m["accounting"]["errors"] for m in payload["methods"])
    unspent = sum(m["audit"]["audit_reserved_unspent"] for m in payload["methods"])

    print()
    print(table)
    print()
    print(f"episodes launched: {launched} / {BUDGET_SLOTS_TOTAL} budget slots")
    print(f"unspent audit allocation: {unspent}")
    print(f"real HighwayEnv episodes completed: {real_rows}")
    print(f"episode errors: {error_rows} ({len(errors)} distinct)")
    for message in errors[:10]:
        print(f"  - {message}")
    print(f"v0.3 path drove the real simulator: {real_rows > 0}")

    (out_dir / "smoke_summary.json").write_text(json.dumps({
        "status": "ok",
        "purpose": "development",
        "seed": SEED,
        "policy": POLICY,
        "total_budget": TOTAL_BUDGET,
        "search_fraction": SEARCH_FRACTION,
        "search_pool": payload["plan"]["search_pool"],
        "audit_candidates": AUDIT_CANDIDATES,
        "audit_repeats": AUDIT_REPEATS,
        "audit_pool": payload["plan"]["audit_pool"],
        "audit_repeats_note": ("connectivity only; NOT a stable-failure criterion and "
                               "not an n=30 protocol"),
        "budget_slots_total": BUDGET_SLOTS_TOTAL,
        "episodes_launched_total": launched,
        "unspent_audit_allocation_total": unspent,
        "real_highwayenv_episodes": real_rows,
        "episode_errors": error_rows,
        "distinct_episode_errors": errors,
        "real_simulator_reached": real_rows > 0,
        "table": table,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

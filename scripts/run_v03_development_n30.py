"""v0.3 formal-shaped DEVELOPMENT integration run (n=30 Final Audit).

Runs the three methods through the **real HighwayEnv** evaluator under the frozen
formal protocol's budget, pools, audit reservation, parameters and scoring, with
exactly two deliberate deviations from `configs/eval_v03_formal.json`:

    purpose      : "evaluation"  ->  "development"
    master seed  : the 20 formal seeds  ->  20261003 (the calibration seed)

Everything else is identical to the frozen protocol: total budget 1000
(700 search + 300 audit), audit_candidates 10, audit_repeats 30, confirm_repeats 2,
pool_fraction 0.25, min_internal_per_candidate 1, max_internal_per_candidate 5,
policy `boundary`.

**This is not a formal evaluation.** No formal evaluation seed is run; the script
refuses to start if the chosen seed appears in the frozen formal seed list. The
run's own manifest records the commit, dirty state and source fingerprint it
actually ran under -- nothing is back-filled afterwards.

Usage:
    .venv\\Scripts\\python.exe scripts\\run_v03_development_n30.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cornercaselab.experiments import (  # noqa: E402
    compare_methods,
    make_simulator_evaluator,
)
from cornercaselab.protocol import (  # noqa: E402
    FORMAL_PROTOCOL_ID,
    formal_method_config,
    formal_plan,
    formal_seeds,
    load_formal_config,
    validate_formal_config,
)

DEVELOPMENT_SEED = 20261003
DEVELOPMENT_PURPOSE = "development"
PROGRESS_EVERY = 100


def progress_evaluator(inner, every: int = PROGRESS_EVERY):
    """Forward every call unchanged, printing periodic progress."""
    counter = {"episodes": 0}

    def evaluator(scenario, sim_seed, policy, settings):
        counter["episodes"] += 1
        if every and counter["episodes"] % every == 0:
            print(f"    ... {counter['episodes']} episodes launched", flush=True)
        return inner(scenario, sim_seed, policy, settings)

    return evaluator, counter


def main() -> int:
    config = load_formal_config()
    summary = validate_formal_config(config)
    plan = formal_plan(config)
    method_config = formal_method_config(config)
    formal = formal_seeds(config)

    if DEVELOPMENT_SEED in formal:
        print(f"BLOCKED: development seed {DEVELOPMENT_SEED} is a formal evaluation seed.")
        return 1

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = ROOT / "runs" / f"v03_dev_n30_{stamp}"
    if out_dir.exists():
        print(f"BLOCKED: refusing to reuse output directory {out_dir}")
        return 1

    print(f"protocol_id={FORMAL_PROTOCOL_ID} (config validated)")
    print(f"development deviation: purpose={DEVELOPMENT_PURPOSE} seed={DEVELOPMENT_SEED}")
    print(f"budget: total={plan.total} search={plan.search_pool} audit={plan.audit_pool} "
          f"(audit {plan.audit_candidates} x {plan.audit_repeats})")
    print(f"methods={config['methods']} policy={config['policy']}")
    print(f"output_dir={out_dir}")
    print(f"formal seeds NOT used: {len(formal)} frozen seeds left untouched")
    sys.stdout.flush()

    evaluator, counter = progress_evaluator(make_simulator_evaluator())
    try:
        payload = compare_methods(
            methods=list(config["methods"]),
            total_budget=plan.total,
            seed=DEVELOPMENT_SEED,
            policy=config["policy"],
            search_fraction=config["budget"]["search_fraction"],
            audit_candidates=plan.audit_candidates,
            audit_repeats=plan.audit_repeats,
            config=method_config,
            evaluator=evaluator,
            purpose=DEVELOPMENT_PURPOSE,
            out=out_dir,
            checkpoint_every=1,
        )
    except BaseException:
        import traceback
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "development_error.json").write_text(
            json.dumps({"status": "blocked",
                        "traceback": traceback.format_exc(),
                        "output_dir": str(out_dir)},
                       indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print("\nBLOCKED: the development integration run did not complete.")
        traceback.print_exc()
        return 1

    (out_dir / "development_override.json").write_text(json.dumps({
        "note": ("Development integration run. Formal-shaped budget and audit, but NOT a "
                 "formal evaluation: two deliberate deviations from the frozen protocol."),
        "protocol_id": FORMAL_PROTOCOL_ID,
        "deviations": {
            "purpose": {"formal": "evaluation", "used": DEVELOPMENT_PURPOSE},
            "master_seed": {"formal": "the 20 frozen paired seeds",
                            "used": DEVELOPMENT_SEED,
                            "reason": "v0.2 calibration seed; development only"},
        },
        "unchanged_from_formal_protocol": {
            "policy": config["policy"],
            "total_episode_budget": plan.total,
            "search_pool": plan.search_pool,
            "audit_pool": plan.audit_pool,
            "audit_candidates": plan.audit_candidates,
            "audit_repeats": plan.audit_repeats,
            "method_parameters": config["method_parameters"],
            "scoring_protocol_id": summary["scoring_protocol_id"],
            "primary_metric": summary["primary_metric"],
        },
        "formal_seeds_used": [],
        "episodes_launched_total": counter["episodes"],
        "written_utc": datetime.now(timezone.utc).isoformat(),
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    header = (f"{'method':<26}{'spent':>7}{'search':>7}{'internal':>9}{'audit':>7}"
              f"{'unspent':>8}{'hits':>6}{'samples':>8}{'errors':>7}")
    print()
    print(header)
    print("-" * len(header))
    for method in payload["methods"]:
        acct = method["accounting"]
        phases = acct["phase_episodes"]
        print(f"{method['method']:<26}{acct['episodes_spent']:>7}"
              f"{phases['search']:>7}{phases['internal_confirm']:>9}"
              f"{phases['final_audit']:>7}"
              f"{method['audit']['audit_reserved_unspent']:>8}"
              f"{method['search_collisions']:>6}{acct['collisions']:>8}"
              f"{acct['errors']:>7}")
    print()
    print(f"total episodes launched: {counter['episodes']}")
    print(f"wrote {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

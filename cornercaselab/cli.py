from __future__ import annotations
import argparse
from dataclasses import asdict
import importlib.util
import json
from pathlib import Path
import sys

from .domain import Scenario, check_seed, sample_scenario, derive_seed
from .experiment import random_pilot, confirmation
from .policy import POLICIES
from .simulator import SimSettings, evaluate, make_environment
from .storage import metadata, fingerprint, read_json, write_json


def integer_at_least_one(value: str) -> int:
    n = int(value)
    if n < 1:
        raise argparse.ArgumentTypeError("Must be >= 1.")
    return n


def seed_arg(value: str) -> int:
    try:
        return check_seed(int(value))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def doctor(smoke: bool, out: Path | None) -> int:
    report = metadata()
    report["python_supported"] = (3, 11) <= sys.version_info[:2] < (3, 14)
    report["sim_dependencies_present"] = all(importlib.util.find_spec(p) is not None
        for p in ("highway_env", "gymnasium", "numpy", "pygame", "PIL"))
    report["smoke"] = "not_requested"
    code = 0
    if smoke:
        try:
            result = evaluate(Scenario(), 12345, settings=SimSettings(horizon_s=1.0))
            report["smoke"] = {k: v for k, v in result.items() if k != "trajectory"}
        except Exception as exc:
            report["smoke"] = {"error": f"{type(exc).__name__}: {exc}"}
            code = 1
    if not report["python_supported"]:
        code = 1
    if out:
        if out.exists():
            raise FileExistsError(f"Refusing to overwrite {out}.")
        write_json(out, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="CornerCaseLab: fixed-budget autonomous-driving scenario testing")
    sub = parser.add_subparsers(dest="command", required=True)
    d = sub.add_parser("doctor", help="Report dependencies; optionally run a 1 s simulator smoke test")
    d.add_argument("--smoke", action="store_true")
    d.add_argument("--out", type=Path)
    s = sub.add_parser("sample", help="Generate explicit scenario JSON without simulation dependencies")
    s.add_argument("--count", type=integer_at_least_one, default=10)
    s.add_argument("--seed", type=seed_arg, default=20261003)
    s.add_argument("--out", type=Path, required=True)
    r = sub.add_parser("run", help="Bounded automatic random pilot; all episodes count toward its budget")
    r.add_argument("--episodes", type=integer_at_least_one, default=100)
    r.add_argument("--seed", type=seed_arg, default=20261003)
    r.add_argument("--policy", choices=POLICIES, default="reactive")
    r.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("replay", help="Rerun a saved case; compare rounded trajectory checksum and termination")
    p.add_argument("case", type=Path)
    p.add_argument("--render", action="store_true")
    p.add_argument("--gif", type=Path)
    p.add_argument("--allow-environment-mismatch", action="store_true")
    c = sub.add_parser("confirm", help="Fresh fixed-n local perturbations; not identical-seed replay")
    c.add_argument("case", type=Path)
    c.add_argument("--repeats", type=integer_at_least_one, default=20)
    c.add_argument("--seed", type=seed_arg, default=20261004)
    c.add_argument("--out", type=Path, required=True)
    c.add_argument("--allow-environment-mismatch", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            return doctor(args.smoke, args.out)
        if args.command == "sample":
            if args.out.exists():
                raise FileExistsError(f"Refusing to overwrite {args.out}.")
            rows = []
            for i in range(args.count):
                scene = sample_scenario(derive_seed(args.seed, "search", i))
                rows.append({"scenario_id": scene.uid, "scenario": scene.to_dict(),
                             "sim_seed": derive_seed(args.seed, "simulation", i)})
            write_json(args.out, {"status": "sampled_only_not_simulated", "trials": rows})
            print(f"Saved {len(rows)} untested scenarios to {args.out}")
            return 0
        if args.command == "run":
            # Validate imports/construction before spending the experiment budget.
            check_env = make_environment(Scenario(), SimSettings())
            check_env.close()
            summary = random_pilot(args.out, args.episodes, args.seed, args.policy, SimSettings())
            print(json.dumps(summary, indent=2))
            return 0
        case = read_json(args.case)
        if case.get("schema") != "ccl-case-v1" or case.get("status") != "ok":
            raise ValueError("Expected an executed ccl-case-v1 JSON record with status=ok.")
        match = fingerprint(metadata()) == fingerprint(case["metadata"])
        if not match and not args.allow_environment_mismatch:
            raise RuntimeError("Recorded environment/source differs. Restore it or explicitly use --allow-environment-mismatch; any such rerun is exploratory, not strict replay.")
        if args.command == "confirm":
            summary = confirmation(case, args.out, args.repeats, args.seed)
            print(json.dumps(summary, indent=2))
            return 0
        result = evaluate(Scenario.from_dict(case["scenario"]), case["sim_seed"], case["policy"],
                          SimSettings(**case["settings"]), render=args.render, gif=args.gif)
        same = (result["trace_sha256_rounded_9dp"] == case["result"]["trace_sha256_rounded_9dp"]
                and result["termination"] == case["result"]["termination"])
        print(json.dumps({"environment_matches": match, "rounded_trajectory_and_termination_match": same,
                          "termination": result["termination"], "gif": str(args.gif) if args.gif else None,
                          "note": "Replay equality does NOT estimate local failure stability."}, indent=2))
        return 0 if same else 1
    except KeyboardInterrupt:
        print("Interrupted; completed case records are preserved.", file=sys.stderr)
        return 130
    except (ValueError, RuntimeError, OSError, ImportError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

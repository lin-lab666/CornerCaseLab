"""A bounded random pilot and a fixed-n independent confirmation stage."""
from __future__ import annotations
from dataclasses import asdict
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

from .domain import Scenario, derive_seed, sample_scenario, perturb_scenario, PERTURBATION
from .metrics import wilson_interval
from .simulator import SimSettings, evaluate
from .storage import append_jsonl, metadata, read_json, write_json

Evaluator = Callable[..., dict[str, Any]]


def run_trials(out: Path, trials: list[dict[str, Any]], *, policy: str,
               settings: SimSettings, purpose: str, extra: dict | None = None,
               evaluator: Evaluator = evaluate) -> dict[str, Any]:
    if not trials:
        raise ValueError("At least one trial is required.")
    # New directory only: no accidental overwrites or fake 'resumed' results.
    out.mkdir(parents=True, exist_ok=False)
    (out/"cases").mkdir()
    meta = metadata()
    write_json(out/"manifest.json", {"purpose": purpose, "status": "running", "metadata": meta,
               "settings": asdict(settings), "policy": policy, "planned_episodes": len(trials),
               "extra": extra or {}, "trials": trials})
    results, error_count, launched = [], 0, 0
    status, start = "running", perf_counter()
    try:
        for i, spec in enumerate(trials):
            case_path = out/"cases"/f"{i:06d}.json"
            launched += 1
            append_jsonl(out/"ledger.jsonl", {"event": "started", "index": i,
                         "scenario_id": spec["scenario_id"], "sim_seed": spec["sim_seed"]})
            record = {"schema": "ccl-case-v1", "index": i, "policy": policy,
                      "settings": asdict(settings), "metadata": meta, **spec}
            try:
                result = evaluator(Scenario.from_dict(spec["scenario"]), spec["sim_seed"],
                                   policy, settings)
                record.update({"status": "ok", "result": result})
                results.append(result)
            except Exception as exc:
                error_count += 1
                record.update({"status": "error", "error": f"{type(exc).__name__}: {exc}"})
                # Dependency/programming failures are not zero-collision results.
                write_json(case_path, record)
                append_jsonl(out/"ledger.jsonl", {"event": "error", "index": i, "error": record["error"]})
                raise
            write_json(case_path, record)
            append_jsonl(out/"ledger.jsonl", {"event": "completed", "index": i,
                         "termination": result["termination"]})
            if (i+1) % 10 == 0 or i+1 == len(trials):
                print(f"[{i+1}/{len(trials)}] collisions={sum(r['ego_collision'] for r in results)}", flush=True)
        status = "complete"
    except BaseException:
        status = "interrupted_or_error"
        raise
    finally:
        collisions = sum(r["ego_collision"] for r in results)
        summary = {"status": status, "purpose": purpose, "planned_episodes": len(trials),
                   "launched_episodes": launched, "valid_completed": len(results),
                   "errors": error_count, "pending_or_interrupted": launched-len(results)-error_count,
                   "ego_collisions": collisions, "goals": sum(r["goal_reached"] for r in results),
                   "offroad": sum(r["termination"] == "offroad" for r in results),
                   "timeouts": sum(r["termination"] == "timeout" for r in results),
                   "wall_time_s": perf_counter()-start,
                   "warning": "Pilot search outcomes, NOT distinct bugs or real-world crash probabilities."}
        if purpose == "local_confirmation" and status == "complete":
            summary["local_collision_fraction"] = collisions/len(results)
            summary["local_fixed_n_wilson95"] = wilson_interval(collisions, len(results))
            summary["warning"] = "Conditional on this base case and declared local perturbation/nuisance distribution; not an anytime bound or road risk."
        write_json(out/"summary.json", summary)
        manifest = read_json(out/"manifest.json")
        manifest["status"] = status
        write_json(out/"manifest.json", manifest)
    return summary


def random_pilot(out: Path, episodes: int, seed: int, policy: str, settings: SimSettings) -> dict:
    if episodes < 1:
        raise ValueError("episodes must be positive.")
    trials = []
    for i in range(episodes):
        scenario = sample_scenario(derive_seed(seed, "search", i))
        trials.append({"scenario": scenario.to_dict(), "scenario_id": scenario.uid,
                       "sim_seed": derive_seed(seed, "simulation", i)})
    return run_trials(out, trials, policy=policy, settings=settings, purpose="random_pilot",
                      extra={"search_seed": seed, "sampler": "independent_uniform_box_v1"})


def confirmation(case: dict, out: Path, repeats: int, seed: int) -> dict:
    if repeats < 1:
        raise ValueError("repeats must be positive.")
    if case.get("status") != "ok":
        raise ValueError("Confirmation needs a successfully executed case record.")
    base = Scenario.from_dict(case["scenario"])
    trials = []
    for i in range(repeats):
        scenario = perturb_scenario(base, derive_seed(seed, "fresh-local", i))
        trials.append({"scenario": scenario.to_dict(), "scenario_id": scenario.uid,
                       "sim_seed": derive_seed(seed, "fresh-nuisance", i)})
    return run_trials(out, trials, policy=case["policy"], settings=SimSettings(**case["settings"]),
                      purpose="local_confirmation", extra={"base_scenario": base.to_dict(),
                      "base_sim_seed": case["sim_seed"], "confirmation_seed": seed,
                      "perturbation_half_widths": PERTURBATION, "fixed_n": repeats})

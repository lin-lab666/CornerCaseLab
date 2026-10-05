"""Frozen v0.3 formal evaluation protocol: read and cross-validate the config.

This module is a **config reader and cross-validator**. It reads
``configs/eval_v03_formal.json`` and checks it against the live code: budget
arithmetic, method parameters, the Final Audit selection rule, the scoring
constants and the derived formal seed list. It changes no algorithm -- the
simulator, controller, budget accounting, resume logic, candidate selection,
seeds, ``BOUNDS`` and ``PERTURBATION`` are untouched.

Protocol ID: ``cornercaselab_v03_eval_v1``.
Authoritative description: ``docs/EVALUATION_PROTOCOL_V03.md``.

Hard gate respected by this module: it only *describes* the formal evaluation.
It never runs it, and it refuses the calibration seed for ``purpose="evaluation"``
via :func:`cornercaselab.experiments.ensure_evaluation_seed`.
"""
from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping

from .budget import SpendPlan, plan_spending
from .candidates import AUDIT_SELECTION_RULE, AUDIT_UNSPENT_RULE
from .experiments import (
    CALIBRATION_SEED,
    METHODS,
    MethodConfig,
    ensure_evaluation_seed,
    internal_confirm_cap,
)
from .policy import POLICIES
from .scoring import (
    PASS_LOWER_BOUND,
    REQUIRED_AUDIT_REPEATS,
    SCORING_PROTOCOL_ID,
    SPATIAL_EXACT_LIMIT,
    WILSON_Z,
    wilson95,
)

FORMAL_PROTOCOL_ID = "cornercaselab_v03_eval_v1"
FORMAL_SEED_COUNT = 20
FORMAL_SEED_PREFIX = "CornerCaseLab-v0.3-formal-"
FORMAL_SEED_DERIVATION = (
    "seed_i = int.from_bytes(sha256(b'CornerCaseLab-v0.3-formal-{i}').digest()[:4], "
    "'big'), i = 0..19"
)

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "configs" / "eval_v03_formal.json"
_DOC_PATH = Path(__file__).resolve().parent.parent / "docs" / "EVALUATION_PROTOCOL_V03.md"

REQUIRED_TOTAL_BUDGET = 1000
REQUIRED_SEARCH_POOL = 700
REQUIRED_AUDIT_POOL = 300
REQUIRED_AUDIT_CANDIDATES = 10
REQUIRED_CONFIRM_REPEATS = 2
REQUIRED_POOL_FRACTION = 0.25
REQUIRED_MIN_INTERNAL = 1
REQUIRED_MAX_INTERNAL = 5
REQUIRED_GLOBAL_CAP = 175
REQUIRED_BOOTSTRAP_RESAMPLES = 10000
REQUIRED_BOOTSTRAP_SEED = 314159265
REQUIRED_PRIMARY_CONTRAST = "adaptive_explore_confirm - random_search"


class ProtocolConfigError(ValueError):
    """Raised when a protocol config disagrees with the frozen protocol or code."""


def derive_formal_seeds(count: int = FORMAL_SEED_COUNT) -> list[int]:
    """The frozen formal master seeds, in their fixed order."""
    return [int.from_bytes(
        sha256(f"{FORMAL_SEED_PREFIX}{index}".encode()).digest()[:4], "big")
        for index in range(count)]


def formal_config_path() -> Path:
    return _CONFIG_PATH


def formal_doc_path() -> Path:
    return _DOC_PATH


def load_formal_config(path: Path | str | None = None) -> dict[str, Any]:
    """Read the frozen config as JSON. Does not validate it."""
    target = Path(path) if path is not None else _CONFIG_PATH
    return json.loads(target.read_text(encoding="utf-8"))


def _collect(problems: list[str], condition: Any, message: str) -> None:
    if not condition:
        problems.append(message)


def validate_formal_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Check a protocol config against the frozen protocol AND the live code.

    Raises :class:`ProtocolConfigError` listing every disagreement, so a partially
    edited or hand-written config cannot be used by accident. Returns a normalised
    summary on success.
    """
    problems: list[str] = []

    def value(*keys: str, default: Any = None) -> Any:
        node: Any = config
        for key in keys:
            if not isinstance(node, Mapping) or key not in node:
                problems.append(f"missing config field: {'.'.join(keys)}")
                return default
            node = node[key]
        return node

    # ---- identity -------------------------------------------------------
    _collect(problems, value("protocol_id") == FORMAL_PROTOCOL_ID,
             f"protocol_id must be {FORMAL_PROTOCOL_ID!r}")
    _collect(problems, value("status") == "frozen", "status must be 'frozen'")
    _collect(problems, value("purpose") == "evaluation", "purpose must be 'evaluation'")
    _collect(problems, value("policy") == "boundary", "policy must be 'boundary'")
    _collect(problems, value("policy") in POLICIES, f"policy must be one of {POLICIES}")
    _collect(problems, value("formal_evaluation_started") is False,
             "formal_evaluation_started must be false in the frozen config")
    _collect(problems, value("calibration_seed_development_only") == CALIBRATION_SEED,
             f"calibration_seed_development_only must be {CALIBRATION_SEED}")

    # ---- budget arithmetic ---------------------------------------------
    total = value("budget", "total_episode_budget")
    search_pool = value("budget", "search_pool")
    audit_pool = value("budget", "audit_pool")
    search_fraction = value("budget", "search_fraction")
    audit_candidates = value("audit", "audit_candidates")
    audit_repeats = value("audit", "audit_repeats")
    _collect(problems, total == REQUIRED_TOTAL_BUDGET,
             f"total_episode_budget must be {REQUIRED_TOTAL_BUDGET}")
    _collect(problems, search_pool == REQUIRED_SEARCH_POOL,
             f"search_pool must be {REQUIRED_SEARCH_POOL}")
    _collect(problems, audit_pool == REQUIRED_AUDIT_POOL,
             f"audit_pool must be {REQUIRED_AUDIT_POOL}")
    _collect(problems, (search_pool or 0) + (audit_pool or 0) == total,
             "search_pool + audit_pool must equal total_episode_budget")
    _collect(problems, value("budget", "renormalise_on_unspent_audit") is False,
             "an unspent audit reservation must never be renormalised away")
    _collect(problems, audit_candidates == REQUIRED_AUDIT_CANDIDATES,
             f"audit_candidates must be {REQUIRED_AUDIT_CANDIDATES}")
    _collect(problems, audit_repeats == REQUIRED_AUDIT_REPEATS,
             f"audit_repeats must be {REQUIRED_AUDIT_REPEATS}")
    _collect(problems, (audit_candidates or 0) * (audit_repeats or 0) == audit_pool,
             "audit_candidates * audit_repeats must equal audit_pool")
    _collect(problems, value("audit", "backfill") == "none",
             "audit backfill must be 'none'")
    _collect(problems, value("audit", "unspent_rule") == AUDIT_UNSPENT_RULE,
             f"audit unspent rule must be {AUDIT_UNSPENT_RULE!r}")

    # The config must reproduce the code's own plan arithmetic.
    if None not in (total, search_fraction, audit_candidates, audit_repeats):
        try:
            plan = plan_spending(total, search_fraction=search_fraction,
                                 audit_candidates=audit_candidates,
                                 audit_repeats=audit_repeats)
            _collect(problems, plan.search_pool == search_pool,
                     f"budget.plan_spending yields search_pool={plan.search_pool}, config says "
                     f"{search_pool}")
            _collect(problems, plan.audit_pool == audit_pool,
                     f"budget.plan_spending yields audit_pool={plan.audit_pool}, config says "
                     f"{audit_pool}")
            _collect(problems, plan.total == total,
                     f"budget.plan_spending yields total={plan.total}, config says {total}")
        except ValueError as exc:
            problems.append(f"budget.plan_spending rejects the frozen budget: {exc}")

    # ---- methods and parameters ----------------------------------------
    _collect(problems, list(value("methods", default=[])) == list(METHODS),
             f"methods must be exactly {list(METHODS)} in that order")
    parameters = value("method_parameters", default={})
    _collect(problems, parameters.get("confirm_repeats") == REQUIRED_CONFIRM_REPEATS,
             f"confirm_repeats must be {REQUIRED_CONFIRM_REPEATS}")
    _collect(problems, parameters.get("pool_fraction") == REQUIRED_POOL_FRACTION,
             f"pool_fraction must be {REQUIRED_POOL_FRACTION}")
    _collect(problems, parameters.get("min_internal_per_candidate") == REQUIRED_MIN_INTERNAL,
             f"min_internal_per_candidate must be {REQUIRED_MIN_INTERNAL}")
    _collect(problems, parameters.get("max_internal_per_candidate") == REQUIRED_MAX_INTERNAL,
             f"max_internal_per_candidate must be {REQUIRED_MAX_INTERNAL}")
    if isinstance(parameters, Mapping) and "min_internal_per_candidate" in parameters \
            and "max_internal_per_candidate" in parameters \
            and parameters["min_internal_per_candidate"] > parameters["max_internal_per_candidate"]:
        problems.append("min_internal_per_candidate must not exceed "
                        "max_internal_per_candidate")
    config_method_config = None
    if isinstance(parameters, Mapping):
        try:
            config_method_config = MethodConfig(
                confirm_repeats=parameters["confirm_repeats"],
                pool_fraction=parameters["pool_fraction"],
                min_internal_per_candidate=parameters["min_internal_per_candidate"],
                max_internal_per_candidate=parameters["max_internal_per_candidate"])
            config_method_config.validate()
        except (KeyError, ValueError) as exc:
            problems.append(f"method_parameters do not form a valid MethodConfig: {exc}")
    if config_method_config is not None and search_pool is not None:
        try:
            plan = plan_spending(total, search_fraction=search_fraction,
                                 audit_candidates=audit_candidates,
                                 audit_repeats=audit_repeats)
            cap = internal_confirm_cap(config_method_config, plan)
            _collect(problems, cap == REQUIRED_GLOBAL_CAP,
                     f"internal confirmation cap is {cap}, config says "
                     f"{parameters.get('global_internal_confirmation_cap')} (expected "
                     f"{REQUIRED_GLOBAL_CAP})")
            _collect(problems,
                     parameters.get("global_internal_confirmation_cap") == REQUIRED_GLOBAL_CAP,
                     f"global_internal_confirmation_cap must be {REQUIRED_GLOBAL_CAP}")
        except ValueError:
            pass

    # ---- scoring --------------------------------------------------------
    scoring_cfg = value("scoring", default={})
    criterion = scoring_cfg.get("stable_criterion", {}) if isinstance(scoring_cfg, Mapping) else {}
    _collect(problems, scoring_cfg.get("scoring_protocol_id") == SCORING_PROTOCOL_ID,
             f"scoring_protocol_id must be {SCORING_PROTOCOL_ID!r}")
    _collect(problems, scoring_cfg.get("required_audit_repeats") == REQUIRED_AUDIT_REPEATS,
             f"scoring.required_audit_repeats must be {REQUIRED_AUDIT_REPEATS}")
    _collect(problems, criterion.get("phase") == "final_audit_only",
             "the stable criterion must use final_audit_only")
    _collect(problems, list(criterion.get("mixes_in", ["?"])) == [],
             "the stable criterion must not mix in any other phase")
    _collect(problems, criterion.get("required_valid_observations") == REQUIRED_AUDIT_REPEATS,
             f"required_valid_observations must be {REQUIRED_AUDIT_REPEATS}")
    _collect(problems, criterion.get("continuity_correction") is False,
             "continuity correction must be false")
    _collect(problems, criterion.get("z") == WILSON_Z, f"z must be {WILSON_Z}")
    _collect(problems, criterion.get("lower_bound_strictly_greater_than") == PASS_LOWER_BOUND,
             f"the lower-bound threshold must be {PASS_LOWER_BOUND}")
    boundary = None
    try:
        boundary = (wilson95(20, 30)[0], wilson95(21, 30)[0])
        _collect(problems, boundary[0] <= PASS_LOWER_BOUND < boundary[1],
                 f"the claimed 20/30-fail 21/30-pass boundary does not hold: {boundary}")
        _collect(problems, criterion.get("minimum_passed_collapses_of_30") == 21,
                 "minimum_passed_collapses_of_30 must be 21")
    except ValueError as exc:  # pragma: no cover - defensive
        problems.append(f"wilson interval unavailable: {exc}")
    primary = scoring_cfg.get("primary_metric", {}) if isinstance(scoring_cfg, Mapping) else {}
    _collect(problems, primary.get("field") == "nonoverlapping_passed_count",
             "the primary metric field must be nonoverlapping_passed_count")
    _collect(problems, primary.get("abbreviation") == "SNY",
             "the primary metric abbreviation must be SNY")
    _collect(problems, primary.get("exact_solver") is True,
             "the primary metric must be solved exactly")
    _collect(problems, primary.get("exact_solver_limit") == SPATIAL_EXACT_LIMIT,
             f"exact_solver_limit must be {SPATIAL_EXACT_LIMIT}")
    _collect(problems,
             isinstance(primary.get("exact_solver_limit"), int)
             and isinstance(audit_candidates, int)
             and primary.get("exact_solver_limit") >= audit_candidates,
             f"the exact SNY solver limit ({primary.get('exact_solver_limit')}) must cover "
             f"audit_candidates ({audit_candidates})")
    caveats = " ".join(primary.get("must_not_be_interpreted_as", [])).lower()
    for forbidden in ("root cause", "probability", "failure region"):
        _collect(problems, forbidden in caveats,
                 f"the primary metric must explicitly disclaim {forbidden!r}")

    # ---- selection semantics -------------------------------------------
    selection = value("final_audit_selection", default={})
    _collect(problems, selection.get("rule_id") == AUDIT_SELECTION_RULE,
             f"selection rule must be {AUDIT_SELECTION_RULE!r}")
    _collect(problems, selection.get("implementation_frozen") is True,
             "final_audit_selection.implementation_frozen must be true")
    key_text = str(selection.get("key", ""))
    for fragment in ("internal_stability", "first_seen_episode", "candidate_id"):
        _collect(problems, fragment in key_text,
                 f"the selection tie-break key must record {fragment!r}")
    _collect(problems, "first-seen" in str(selection.get("random_search_fallback", "")),
             "the random_search fallback must be recorded as first-seen")

    # ---- seeds ----------------------------------------------------------
    seeds_cfg = value("seeds", default={})
    seeds = list(seeds_cfg.get("values", [])) if isinstance(seeds_cfg, Mapping) else []
    derived = derive_formal_seeds(FORMAL_SEED_COUNT)
    _collect(problems, seeds_cfg.get("count") == FORMAL_SEED_COUNT,
             f"seeds.count must be {FORMAL_SEED_COUNT}")
    _collect(problems, seeds_cfg.get("paired") is True, "seeds.paired must be true")
    _collect(problems, len(seeds) == FORMAL_SEED_COUNT,
             f"exactly {FORMAL_SEED_COUNT} seed values are required, got {len(seeds)}")
    _collect(problems, len(set(seeds)) == len(seeds), "formal seeds must all be unique")
    _collect(problems, all(isinstance(s, int) and 0 <= s < 2 ** 32 for s in seeds),
             "every formal seed must be an integer in [0, 2**32)")
    _collect(problems, CALIBRATION_SEED not in seeds,
             "the calibration seed must not appear among the formal seeds")
    if seeds != derived:
        problems.append("the stored formal seeds do not match their documented derivation "
                        "(values or order differ)")

    # ---- reporting ------------------------------------------------------
    reporting = value("reporting", default={})
    interval = reporting.get("interval", {}) if isinstance(reporting, Mapping) else {}
    _collect(problems, interval.get("resamples") == REQUIRED_BOOTSTRAP_RESAMPLES,
             f"bootstrap resamples must be {REQUIRED_BOOTSTRAP_RESAMPLES}")
    _collect(problems, interval.get("bootstrap_seed") == REQUIRED_BOOTSTRAP_SEED,
             f"bootstrap seed must be {REQUIRED_BOOTSTRAP_SEED}")
    _collect(problems, interval.get("confidence") == 0.95, "bootstrap confidence must be 0.95")
    _collect(problems, "percentile paired bootstrap" in str(interval.get("method", "")),
             "the interval method must be the percentile paired bootstrap")
    _collect(problems,
             list(reporting.get("paired_contrasts_primary", [])) == [REQUIRED_PRIMARY_CONTRAST],
             f"the primary paired contrast must be exactly [{REQUIRED_PRIMARY_CONTRAST!r}]")
    _collect(problems, "median" in list(reporting.get("summary_statistics", [])),
             "summary statistics must include the median")
    _collect(problems, "iqr" in list(reporting.get("summary_statistics", [])),
             "summary statistics must include the IQR")
    _collect(problems, "p-values are not the primary criterion"
             in str(reporting.get("primary_success_criterion", "")),
             "the reporting plan must state that p-values are not the primary criterion")

    if problems:
        raise ProtocolConfigError(
            "formal protocol config does not match the frozen protocol:\n  - "
            + "\n  - ".join(problems))

    return {
        "protocol_id": config["protocol_id"],
        "total_episode_budget": total,
        "search_pool": search_pool,
        "audit_pool": audit_pool,
        "audit_candidates": audit_candidates,
        "audit_repeats": audit_repeats,
        "global_internal_confirmation_cap": REQUIRED_GLOBAL_CAP,
        "primary_metric": primary.get("abbreviation"),
        "primary_metric_field": primary.get("field"),
        "scoring_protocol_id": scoring_cfg.get("scoring_protocol_id"),
        "seed_count": len(seeds),
        "seeds": seeds,
        "wilson_boundary": boundary,
        "method_config": config_method_config,
    }


def formal_plan(config: Mapping[str, Any]) -> SpendPlan:
    """The frozen spend plan (validates the config first)."""
    validate_formal_config(config)
    return plan_spending(config["budget"]["total_episode_budget"],
                         search_fraction=config["budget"]["search_fraction"],
                         audit_candidates=config["audit"]["audit_candidates"],
                         audit_repeats=config["audit"]["audit_repeats"])


def formal_method_config(config: Mapping[str, Any]) -> MethodConfig:
    """The frozen method parameters (validates the config first)."""
    validate_formal_config(config)
    parameters = config["method_parameters"]
    return MethodConfig(confirm_repeats=parameters["confirm_repeats"],
                        pool_fraction=parameters["pool_fraction"],
                        min_internal_per_candidate=parameters["min_internal_per_candidate"],
                        max_internal_per_candidate=parameters["max_internal_per_candidate"])


def formal_seeds(config: Mapping[str, Any]) -> list[int]:
    """The frozen formal seeds in their fixed order (validates the config first)."""
    validate_formal_config(config)
    return list(config["seeds"]["values"])


def check_evaluation_seed(seed: int) -> int:
    """Refuse the calibration seed for formal evaluation.

    Delegates to :func:`cornercaselab.experiments.ensure_evaluation_seed` with the
    frozen ``purpose``, so the hard gate is enforced by the existing code path.
    """
    return ensure_evaluation_seed(seed, "evaluation")


def documentation_problems(config: Mapping[str, Any],
                           doc_path: Path | str | None = None) -> list[str]:
    """Return the ways ``docs/EVALUATION_PROTOCOL_V03.md`` disagrees with the config.

    Used by the protocol tests so the document, the config and the code cannot
    drift apart silently.
    """
    target = Path(doc_path) if doc_path is not None else _DOC_PATH
    if not target.is_file():
        return [f"protocol document not found: {target}"]
    text = target.read_text(encoding="utf-8")
    problems: list[str] = []

    def require(fragment: str, label: str) -> None:
        if fragment not in text:
            problems.append(f"{label} missing from {target.name}: {fragment!r}")

    require(config["protocol_id"], "protocol id")
    require(config["scoring"]["scoring_protocol_id"], "scoring protocol id")
    require(config["policy"], "policy")
    require(str(config["budget"]["total_episode_budget"]), "total budget")
    require(str(config["budget"]["search_pool"]), "search pool")
    require(str(config["budget"]["audit_pool"]), "audit pool")
    require(str(config["audit"]["audit_candidates"]), "audit_candidates")
    require(str(config["audit"]["audit_repeats"]), "audit_repeats")
    require(str(config["method_parameters"]["global_internal_confirmation_cap"]),
            "global internal confirmation cap")
    require(str(config["reporting"]["interval"]["bootstrap_seed"]), "bootstrap seed")
    require(str(config["reporting"]["interval"]["resamples"]), "bootstrap resamples")
    require(config["scoring"]["primary_metric"]["abbreviation"], "primary metric name")
    require(config["scoring"]["primary_metric"]["field"], "primary metric field")
    require(AUDIT_SELECTION_RULE, "frozen selection rule id")
    for seed in config["seeds"]["values"]:
        require(str(seed), "formal seed")
    for fragment in ("first_seen_episode", "candidate_id", "internal_stability"):
        require(fragment, "selection tie-break key fragment")
    for fragment in ("distinct root causes", "failure region", "probability"):
        require(fragment, "primary metric disclaimer")
    require("READY FOR FORMAL EVALUATION", "readiness report section")
    return problems

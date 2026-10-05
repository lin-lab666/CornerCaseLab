"""v0.3 checkpoint: the minimal, real state needed to resume a method run.

A checkpoint is written between episodes. It holds everything a resumed run
needs to continue without repeating a launched episode, reusing a seed, or
changing the deterministic seed stream:

* method / purpose / master seed / policy / settings
* the spending plan and the method configuration
* the whole :class:`~cornercaselab.budget.EpisodeBudget` ledger
* the :class:`~cornercaselab.candidates.CandidateState`
* the scheduling counters (:class:`RunState`), including the pre-computed audit
  worklist once the run has entered the audit stage
* reproducibility metadata

The callables of ``SearchProblem`` (sampler/perturb) are code, not state; a
resumed run reuses the current defaults, so resuming with a custom sampler or
perturbation function is only valid if the caller passes the same one again.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .budget import EpisodeBudget
from .candidates import CandidateState
from .storage import read_json, write_json

CHECKPOINT_SCHEMA = "ccl-v03-checkpoint-v1"

STAGE_SEARCH = "search"
STAGE_AUDIT = "audit"
STAGE_COMPLETE = "complete"
STAGES = (STAGE_SEARCH, STAGE_AUDIT, STAGE_COMPLETE)


@dataclass
class RunState:
    """Scheduling counters and the pre-computed audit plan."""
    method: str
    stage: str = STAGE_SEARCH
    search_index: int = 0
    audit_selected: list[str] = field(default_factory=list)
    audit_work: list[list[Any]] = field(default_factory=list)
    audit_cursor: int = 0
    stop_reason: str | None = None
    min_internal_unmet: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RunState:
        stage = value["stage"]
        if stage not in STAGES:
            raise ValueError(f"Unknown checkpoint stage {stage!r}; choose {STAGES}.")
        return cls(
            method=value["method"], stage=stage,
            search_index=int(value["search_index"]),
            audit_selected=list(value["audit_selected"]),
            audit_work=[list(row) for row in value["audit_work"]],
            audit_cursor=int(value["audit_cursor"]),
            stop_reason=value.get("stop_reason"),
            min_internal_unmet=list(value.get("min_internal_unmet", [])),
        )


@dataclass
class RunCheckpoint:
    """One durable snapshot of a method run."""
    method: str
    purpose: str
    seed: int
    policy: str
    settings: dict[str, Any]
    plan: dict[str, Any]
    config: dict[str, Any]
    budget: dict[str, Any]
    candidates: dict[str, Any]
    run: dict[str, Any]
    metadata: dict[str, Any]
    schema: str = CHECKPOINT_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RunCheckpoint:
        if value.get("schema") != CHECKPOINT_SCHEMA:
            raise ValueError(
                f"Unsupported checkpoint schema {value.get('schema')!r}; "
                f"expected {CHECKPOINT_SCHEMA!r}."
            )
        return cls(
            method=value["method"], purpose=value["purpose"], seed=int(value["seed"]),
            policy=value["policy"], settings=dict(value["settings"]),
            plan=dict(value["plan"]), config=dict(value["config"]),
            budget=dict(value["budget"]), candidates=dict(value["candidates"]),
            run=dict(value["run"]), metadata=dict(value["metadata"]),
        )

    # ------------------------------------------------------------- rebuild
    def restore_budget(self) -> EpisodeBudget:
        return EpisodeBudget.from_dict(self.budget)

    def restore_candidates(self) -> CandidateState:
        return CandidateState.from_dict(self.candidates)

    def restore_run_state(self) -> RunState:
        return RunState.from_dict(self.run)


def save_checkpoint(path: Path | str, checkpoint: RunCheckpoint) -> Path:
    """Atomically write a checkpoint (creating parent directories)."""
    target = Path(path)
    write_json(target, checkpoint.to_dict())
    return target


def load_checkpoint(path: Path | str) -> RunCheckpoint:
    """Read a checkpoint written by :func:`save_checkpoint`."""
    return RunCheckpoint.from_dict(read_json(Path(path)))

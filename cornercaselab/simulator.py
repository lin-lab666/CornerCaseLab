"""HighwayEnv adapter. Imported only when a simulation is explicitly requested.

The upstream road and vehicle dynamics are reused, not claimed as original.
Validated source interface: HighwayEnv tag v1.10.2. Integration status is recorded
in docs/VALIDATION.md, separately from standard-library unit tests.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from hashlib import sha256
import math
import os
from pathlib import Path
from time import perf_counter
from typing import Any

from .domain import Scenario, canonical_json, check_seed
from .metrics import longitudinal_ttc
from .policy import action_for


@dataclass(frozen=True)
class SimSettings:
    horizon_s: float = 25.0
    simulation_hz: int = 15
    policy_hz: int = 5

    def __post_init__(self) -> None:
        if not math.isfinite(self.horizon_s) or self.horizon_s <= 0:
            raise ValueError("horizon_s must be positive and finite.")
        if self.simulation_hz < 1 or self.policy_hz < 1 or self.simulation_hz % self.policy_hz:
            raise ValueError("simulation_hz must be a positive multiple of policy_hz.")
        if not math.isclose(self.horizon_s*self.policy_hz, round(self.horizon_s*self.policy_hz)):
            raise ValueError("horizon_s * policy_hz must be an integer.")


def make_environment(scenario: Scenario, settings: SimSettings, render_mode: str | None = None):
    os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
    if render_mode is None:
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    elif render_mode == "rgb_array":
        # RGB frame capture needs a functioning video backend on Windows.
        # Keep audio headless, but do not force SDL's dummy video driver.
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    try:
        from highway_env.envs.merge_env import MergeEnv
        from highway_env.vehicle.behavior import IDMVehicle
    except ImportError as exc:
        raise RuntimeError('Simulation dependencies missing. Run: python -m pip install -e ".[sim]"') from exc

    class ParameterisedMerge(MergeEnv):
        def _make_vehicles(self):
            p = Scenario.from_dict(self.config["ccl_scenario"])
            main_lane = self.road.network.get_lane(("a", "b", 1))
            ramp_lane = self.road.network.get_lane(("j", "k", 0))
            self.vehicle = self.action_type.vehicle_class(
                self.road, main_lane.position(30.0, 0), speed=p.ego_speed)
            lead = IDMVehicle(self.road, main_lane.position(30.0+p.front_gap+5.0, 0),
                              speed=p.front_speed, target_speed=p.front_speed,
                              enable_lane_change=False)
            ramp = IDMVehicle(self.road, ramp_lane.position(p.ramp_x, 0),
                              speed=p.ramp_speed, target_speed=p.ramp_target_speed)
            # Explicit seeded nuisance variation; do not rely on a seed label alone.
            lead.randomize_behavior()
            ramp.randomize_behavior()
            self.road.vehicles = [self.vehicle, lead, ramp]

        def _is_terminated(self):
            return bool(self.vehicle.crashed or not self.vehicle.on_road or self.vehicle.position[0] > 370.0)

        def _is_truncated(self):
            return bool(self.time >= self.config["duration"]-1e-9)

    return ParameterisedMerge(config={
        "ccl_scenario": scenario.to_dict(), "duration": settings.horizon_s,
        "simulation_frequency": settings.simulation_hz, "policy_frequency": settings.policy_hz,
        "observation": {"type": "Kinematics", "vehicles_count": 6,
                        "features": ["presence", "x", "y", "vx", "vy"],
                        "absolute": True, "normalize": False, "clip": False,
                        "see_behind": True, "order": "sorted", "include_obstacles": False},
        "action": {"type": "DiscreteMetaAction", "longitudinal": True, "lateral": True,
                   "target_speeds": list(range(0, 36, 5))},
        "screen_width": 900, "screen_height": 250,
        "offscreen_rendering": render_mode != "human",
        "real_time_rendering": render_mode == "human",
    }, render_mode=render_mode)


def _snapshot(env, action: int | None) -> dict[str, Any]:
    return {"time_s": round(float(env.time), 9), "action": action,
            "vehicles": [{"x": round(float(v.position[0]), 9), "y": round(float(v.position[1]), 9),
                          "speed": round(float(v.speed), 9), "heading": round(float(v.heading), 9),
                          "crashed": bool(v.crashed)} for v in env.road.vehicles]}


def evaluate(scenario: Scenario, sim_seed: int, policy: str = "reactive",
             settings: SimSettings | None = None, *, render: bool = False,
             gif: Path | None = None) -> dict[str, Any]:
    check_seed(sim_seed)
    settings = settings or SimSettings()
    if render and gif:
        raise ValueError("Use either a live render window or GIF, not both.")
    if gif and gif.exists():
        raise FileExistsError(f"Refusing to overwrite {gif}.")
    mode = "human" if render else ("rgb_array" if gif else None)
    start = perf_counter()
    env = make_environment(scenario, settings, mode)
    frames, trace = [], []
    try:
        obs, _ = env.reset(seed=sim_seed)
        # All scene actors start separated by construction; no claim of avoidability.
        initial_deltas = [float(v.DELTA) for v in env.road.vehicles[1:]]
        trace.append(_snapshot(env, None))
        min_ttc = longitudinal_ttc(obs)
        total_reward = 0.0
        steps = 0
        if gif:
            from PIL import Image
            frames.append(Image.fromarray(env.render()).convert("RGB"))
        max_steps = round(settings.horizon_s*settings.policy_hz)
        for steps in range(1, max_steps+1):
            action = action_for(obs, policy)
            obs, reward, terminated, truncated, _ = env.step(action)
            total_reward += float(reward)
            trace.append(_snapshot(env, action))
            ttc = longitudinal_ttc(obs)
            if ttc is not None:
                min_ttc = ttc if min_ttc is None else min(min_ttc, ttc)
            if gif:
                frames.append(Image.fromarray(env.render()).convert("RGB"))
            if terminated or truncated or (render and env.done):
                break
        crashed, on_road = bool(env.vehicle.crashed), bool(env.vehicle.on_road)
        goal = bool(env.vehicle.position[0] > 370.0) and not crashed and on_road
        reason = "collision" if crashed else ("offroad" if not on_road else ("goal" if goal else "timeout"))
        if render and env.done and not (terminated or truncated):
            reason = "user_closed"
        if gif:
            gif.parent.mkdir(parents=True, exist_ok=True)
            frames[0].save(gif, save_all=True, append_images=frames[1:],
                           duration=round(1000/settings.policy_hz), loop=0)
        return {"termination": reason, "ego_collision": crashed, "goal_reached": goal,
                "npc_collisions": sum(bool(v.crashed) for v in env.road.vehicles[1:]),
                "sim_time_s": float(env.time), "policy_steps": steps,
                "min_forward_ttc_sampled_s": min_ttc, "reward_sum": total_reward,
                "wall_time_s": perf_counter()-start, "initial_npc_delta": initial_deltas,
                "trace_sha256_rounded_9dp": sha256(canonical_json(trace).encode()).hexdigest(),
                "trajectory": trace}
    finally:
        env.close()

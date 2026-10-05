# Initial related-work notes

Checked 2026-10-03 using official documentation, project pages and research-paper
abstracts/pages. This is a **starting reading list**, not a completed full-text
survey or evidence that the proposed contribution is novel.

| Source | What is already covered | Consequence for CornerCaseLab |
|---|---|---|
| DriveFuzz (2022) | Driving-quality-guided mutation and end-to-end testing of driving systems | Automated hazardous-scenario search itself is not new. |
| SafeBench (2022, autonomous-vehicle benchmark) | Safety-critical scenarios and common evaluation infrastructure | Building a testing wrapper alone is not a method contribution. |
| Causality-driven Testing of Autonomous Driving Systems (2024) | Causal-model-guided testing, adaptive requirement-coverage fitness, and fixed-budget comparisons | “Adaptive” or “fixed budget” alone cannot establish originality. |
| SaFeR (2026) | Safety-critical generation with feasibility-constrained token resampling | Adding feasibility constraints is not by itself a new idea. |

Primary references:

- DriveFuzz: Discovering Autonomous Driving Bugs through Driving Quality-Guided Fuzzing.
  https://arxiv.org/abs/2211.01829
  Project: https://github.com/dk-kling/drivefuzz
- SafeBench: A Benchmarking Platform for Safety Evaluation of Autonomous Vehicles.
  https://arxiv.org/abs/2206.09682
  Project: https://safebench.github.io/
  Do not confuse this with the unrelated multimodal-model safety benchmark sharing the name.
- Causality-driven Testing of Autonomous Driving Systems.
  https://doi.org/10.1145/3635709
- SaFeR: Safety-Critical Scenario Generation for Autonomous Driving Test via Feasibility-Constrained Token Resampling.
  https://arxiv.org/abs/2603.04071

Implementation interface references:

- HighwayEnv installation: https://highway-env.farama.org/installation/
- The deliberately selected release: https://pypi.org/project/highway-env/1.10.2/
- Pinned merge environment source:
  https://github.com/Farama-Foundation/HighwayEnv/blob/v1.10.2/highway_env/envs/merge_env.py
- Pinned observation source:
  https://github.com/Farama-Foundation/HighwayEnv/blob/v1.10.2/highway_env/envs/common/observation.py
- Pinned vehicle behavior source:
  https://github.com/Farama-Foundation/HighwayEnv/blob/v1.10.2/highway_env/vehicle/behavior.py
- Gymnasium 1.2.2: https://pypi.org/project/gymnasium/1.2.2/

## Next reading questions

For the closest work, inspect the full method and experiments, not only the abstract:
Does it distinguish exact replay from neighbourhood failure stability? Does its
budget include all replication, final audit and model overhead? How is duplicate
failure defined? How is simulation randomness sampled? Does it use sequential
replication/active testing already? Are its uncertainty statements valid under
its adaptive sampling/stopping rules? Could its method be fairly adapted here?

The proposed exploration-versus-local-replication formulation is a hypothesis to
investigate. It has **not** been established as an unoccupied research niche.

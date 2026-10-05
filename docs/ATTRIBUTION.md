# Attribution and publication notice

HighwayEnv supplies the road geometry, vehicle dynamics, background IDM behavior,
action implementation and renderer. Gymnasium supplies the environment interface.
NumPy and upstream dependencies supply numerical/runtime functionality. Pillow
is used only for optional GIF output. Their authors retain their respective
attribution and licenses; dependencies are installed, not bundled in this archive.

## Dependency usage

Every third-party library is used as an **installed dependency**, through its public
interface. None is copied into, vendored in, or redistributed by this repository, so the
license of each one applies to that library only.

| Dependency | How it is used here | Bundled / redistributed? |
|---|---|---|
| HighwayEnv | imported and subclassed through its documented API in `cornercaselab/simulator.py` | No |
| Gymnasium | environment interface, reached through HighwayEnv | No |
| NumPy | numerical/runtime functionality (transitive) | No |
| Pillow | optional GIF output from the replay command | No |
| matplotlib | **directly used** by the tracked figure renderer `scripts/render_formal_results_v03.py` | No |
| pygame | transitive dependency of HighwayEnv rendering; **not imported directly** by this repository | No |
| pandas, scipy | **not** direct dependencies of this project. They may happen to be present in a given environment and are then recorded in run metadata; no tracked file imports either of them. | No |

The CornerCaseLab starter implements scenario specifications/sampling, a small
scenario adapter, observation-only pilot rules, trial accounting, provenance,
replay checks, independent local confirmation and tests. These implementation
components are not a verified original research algorithm.

This starter was prepared with AI assistance. The project owner should review,
understand, revise and validate it, keep an accurate development record, and
follow any future venue's disclosure requirements. Do not claim that the whole
simulator or upstream behavioral models were independently authored here.

No remote GitHub repository has been created and nothing has been published. The
project owner is 吴昊霖 (GitHub: lin-lab666); the provenance and copyright record is in
[docs/PROVENANCE.md](PROVENANCE.md).

CornerCaseLab's own files are licensed under the **Apache License, Version 2.0**
(see [LICENSE](../LICENSE)). That license covers **only CornerCaseLab's own files**.
Every third-party dependency remains under its own license and is not relicensed by this
project. Because no third-party source is redistributed here, no `NOTICE` file is
required; retain all applicable third-party notices listed above regardless.

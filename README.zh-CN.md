# CornerCaseLab 中文说明

**面向自动驾驶策略的预算感知场景测试。**

当前状态：**v0.3 正式评估已完成** —— 20 个冻结配对种子 × 3 种方法 × 1000 个 episode 名额
= **60,000 次真实 HighwayEnv 仿真**，其中 0 次仿真错误、0 次中断、0 个 Final Audit 不完整候选。

English README: [README.md](README.md)

## 研究问题

在**固定的仿真预算**下，测试系统应该如何在「探索新场景」和「确认已有失败是否在小幅扰动下
依然失败」之间分配次数？

这就是本仓库研究的问题。每种方法在每个种子上拿到的仿真次数完全相同，因此比较的是**预算怎么
分配**，而不是预算有多少。探索只能产生候选；确认会消耗预算，但可能让被提名的候选更容易通过
独立审计。CornerCaseLab 同时实现了这条分界线两侧的机制、一个与提名步骤相互独立的审计流程，
以及一个空间打分规则，并在同一份冻结协议下测量它们。

这是研究项目，不是产品，也不构成任何关于真实道路安全的结论。请阅读
[局限](#局限) 与 [结果解读](#结果解读)。

## 三种方法

三种方法预算完全相同，并且在同一 master seed 上配对。

| 方法 | 搜索阶段 | 提名规则 |
|---|---|---|
| **Random Search** | 每个搜索 episode 重新均匀采样一个场景 | 先见先得（没有内部稳定性估计） |
| **Fixed Explore/Confirm** | 每个候选做 `confirm_repeats = 2` 次内部确认 | 按内部稳定性排序，其次先见先得 |
| **Adaptive Explore/Confirm** | `pool_fraction = 0.25` → 内部确认全局上限 `floor(0.25 × 700) = 175`，`min_internal_per_candidate = 1`，`max_internal_per_candidate = 5` | 按内部稳定性排序，其次先见先得 |

提名本身就是方法的一部分：三种方法在「选哪些候选进入 Final Audit」时掌握的信息**刻意不同**。
冻结的 tie-break 为 `rank_by_internal_stability_desc_then_first_seen_asc_v1`。

## 实验协议

冻结协议 ID **`cornercaselab_v03_eval_v1`**；打分子协议 **`v03_scoring_v1`**。

| 项目 | 数值 |
|---|---|
| 冻结配对 master seed | **20** 个（`policy = boundary`、`purpose = evaluation`） |
| 每个种子的方法数 | 3（配对使用同一 master seed） |
| 每方法每 seed 分配名额 | **1000** |
| 搜索 + 内部确认池 | **700** |
| Final Audit 池 | **300** = 10 个候选 × 30 次 |
| 总 episode 数 | **60,000** |
| 错误 / 中断 / Final Audit 不完整候选 | **0 / 0 / 0** |

**Final Audit。** 被提名的 10 个候选各用新的背景随机性重测 30 次。审计消耗与搜索共用同一个预算
计数器；没有用掉的审计名额不会被挪用到别处，也不会补跑
（`no_backfill_leave_unused_audit_reservation_unspent_v1`）。

**稳定候选（stable candidate）**：恰好有 30 个有效 Final Audit 观测，并且其碰撞比例的
Wilson 双侧 95% 区间（不做连续性校正）**下界 > 0.5** —— 等价于 **≥ 21/30** 次审计发生自车碰撞。
有效观测不足 30 次时记为 `incomplete`，稳定性判定保持 null（`stable = null`），**不会记为 false**。

种子是推导出来的，不是挑选的：`seed_i = SHA256("CornerCaseLab-v0.3-formal-{i}")` 的前四字节按
big-endian 解释，`i = 0..19`。标定种子 `20261003` 只保留给 `purpose = development` 的运行，
在 evaluation 下会被直接拒绝。

详见 [`docs/EVALUATION_PROTOCOL_V03.md`](docs/EVALUATION_PROTOCOL_V03.md)、
[`docs/SCORING_PROTOCOL_V03.md`](docs/SCORING_PROTOCOL_V03.md) 与
[`configs/eval_v03_formal.json`](configs/eval_v03_formal.json)。

## 主指标 SNY

**Stable Neighborhood Yield (SNY)** = `nonoverlapping_passed_count`：通过 Final Audit 的候选之间，
**精确求解**出的两两不重叠邻域数量的最大值。邻域是冻结的六参数扰动空间中的轴对齐盒子，重叠关系
用精确最大独立集求解并采用字典序 tie-break，**不是**贪心近似。

> **SNY 只是一个代理指标。** 它**不是**根因数量，**不是**真实碰撞概率，**也不是**真实
> failure region 的数量。场景 hash 标识的是参数，不是独立的 bug 或失败原因。

## 正式结果

20 个配对种子的平均 SNY（逐 seed 原始值见
[`raw_sny_by_seed.csv`](reports/formal_v03/raw_sny_by_seed.csv)）：

| 方法 | n | **平均 SNY** | 中位数 | 标准差 (ddof 1) | IQR |
|---|---|---|---|---|---|
| Random Search | 20 | **4.75** | 5.00 | 1.446 | 2.00 |
| Fixed Explore/Confirm | 20 | **6.05** | 6.00 | 1.432 | 2.00 |
| Adaptive Explore/Confirm | 20 | **7.10** | 7.00 | 1.373 | 0.50 |

冻结的配对对比 —— percentile **paired** bootstrap，**10,000** 次重采样，分析 seed
**314159265**，95% 置信区间：

| 角色 | 对比 | 平均差 | 95% 置信区间 |
|---|---|---|---|
| **主对比** | Adaptive − Random | **+2.35** | **[1.95, 2.75]** |
| 次对比 | Adaptive − Fixed | **+1.05** | **[0.55, 1.55]** |
| 次对比 | Fixed − Random | **+1.30** | **[0.75, 1.85]** |

主要判据是**效应量及其不确定性**；三个区间在冻结的 SNY 尺度上都完全位于 0 以上。
**本仓库不报告 p 值，也不把 p 值作为判据** —— 冻结的分析计划中没有定义它。完整数字见
[`table_primary_results.csv`](reports/formal_v03/table_primary_results.csv)、
[`table_paired_contrasts.csv`](reports/formal_v03/table_paired_contrasts.csv)。

## 结果图

![逐 seed 配对 SNY](reports/formal_v03/figure_1_paired_sny_by_seed.png)

*20 个冻结种子的配对 SNY：同一个 seed 同时出现在三条序列中，因此可以直接看出每个 seed 下的
相对关系；没有平滑曲线，也没有隐藏任何 seed。*

![配对差值与冻结置信区间](reports/formal_v03/figure_3_paired_differences.png)

*逐 seed 的原始配对差值，叠加平均差、冻结的 95% 配对 bootstrap 置信区间与零参考线；主对比
已明确标出。*

![搜索到审计的漏斗](reports/formal_v03/figure_4_search_to_audit_funnel.png)

*描述性漏斗：平均 unique search collision 候选数 → 10 个受审候选 → 平均通过数 → 平均 SNY。
这只是描述工具，不是新增的假设检验。*

另外三张图（SNY 分布、预算分配、审计通过比例）见
[`reports/formal_v03/`](reports/formal_v03)。

## 结果解读

**确证性（CONFIRMATORY，已冻结、预先登记）**：上面报告的 SNY 分析、三组配对对比及其
bootstrap 区间。

**描述性（DESCRIPTIVE，同一份冻结数据，不做推断）**：

| 指标 | Random | Fixed | Adaptive |
|---|---|---|---|
| 平均 unique search collision 候选数 / seed | **87.20** | **71.15** | **67.70** |
| Final Audit 通过比例 | **95/200 = 47.5%** | **121/200 = 60.5%** | **142/200 = 71.0%** |
| 平均内部确认 episode / seed | 0.00 | 142.15 | **175.00** |
| 达到 175 上限的 seed 数 | — | 0/20 | **20/20** |

Random Search 平均发现了**更多** search collision 候选，但它被提名的候选里通过独立
Final Audit 的比例**更低**。Fixed 与 Adaptive 把搜索池的一部分用来做内部确认、而不是继续探索
新场景，因此产生的不同候选更少。在冻结协议下，Adaptive 取得了最高的 SNY。

**探索性（EXPLORATORY，非预先登记）。** 这些结果**与「广度–稳定性权衡」相一致**：把一部分预算
用于内部确认，可能提高提名质量，代价是探索广度。这只是假设，不是已经被确立的机制。方法身份同时
捆绑了提名规则、预算划分和信息集，因此本设计**无法**把效果单独归因于「确认」这一步，也没有运行
任何消融实验。各方法内部，「候选数 vs SNY」的逐 seed 相关性都较弱且为负（Adaptive −0.06、
Random −0.09、Fixed −0.21；仅描述性，n = 20，未做检验）。

另有一个描述性事实：本次正式实验中，**全部 60 个 seed–method 组合**都满足
`passed_candidate_count == SNY`。也就是说：**在冻结的 neighbourhood scale 下，没有观察到
passed candidates 之间存在需要由空间去重消除的 overlap。** 这**不**代表这些候选具有不同的
根因。

完整叙述（明确标注 CONFIRMATORY / DESCRIPTIVE / EXPLORATORY，并列出不被支持的结论）见
[`RESULTS_INTERPRETATION.md`](reports/formal_v03/RESULTS_INTERPRETATION.md)。

## 复现方式

公开版本来源说明：[`docs/PUBLIC_RELEASE.md`](docs/PUBLIC_RELEASE.md)。本 public 仓库是经过
脱敏处理的 v0.3.0 release snapshot；完整的原始研究与开发 Git 历史保存在 private research
archive 中，不在此公开发布。

本仓库纳入版本管理的内容：冻结配置、两份协议文档、仿真/打分/分析的实现、测试套件，以及
**派生结果**（图、表、逐 seed 原始 SNY、描述性 JSON）和一份数据冻结清单。

**刻意不入库**的内容：正式运行本身（`runs/v03_formal_eval_…`，218 MB 的 ledger、checkpoint
与逐候选记录）。它按设计被 Git 忽略，因此新克隆的仓库可以读到全部已发布的数字，但如果不重新跑
仿真，就无法对原始 episode 重新打分。

* **数据冻结** —— [`FORMAL_DATA_FREEZE.json`](reports/formal_v03/FORMAL_DATA_FREEZE.json) 与
  [`formal_data_file_sha256.csv`](reports/formal_v03/formal_data_file_sha256.csv) 记录了正式运行
  **全部 415 个文件**的 SHA256、关键 manifest、冻结配置、分析输出、种子顺序，以及每一批的来源。
* **离线重算** —— `scripts/render_formal_results_v03.py` 只读取已保存结果，用**两套独立实现**
  （冻结的分析代码 + 独立编写的 bootstrap）重算每一个确证数字；只要有任何数字不一致，就
  **非零退出且不写入任何文件**。它不会启动任何仿真 episode。运行它需要原始运行目录存在。
* **来源记录** —— seeds 1–5 在 commit `c7b2d01` 下产生，seeds 6–20 在 `f580d02` 下产生，
  所报告的分析在 `25c86cd` 下产生。这三个 commit 的差异**仅在**
  `scripts/run_v03_formal.py`（编排层）；协议、仿真、打分与分析实现三者完全一致。

```powershell
# 环境与完整测试套件（272 项）
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[sim]"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m cornercaselab doctor --smoke

# 正式评估驱动（20 个配对种子、60,000 次 episode；不会改动冻结协议，
# 任何完整性失败都会停机）
.\.venv\Scripts\python.exe scripts\run_v03_formal.py

# 对已保存的运行做离线打分
.\.venv\Scripts\python.exe scripts\score_v03.py --input runs\<运行目录> --out runs\<新目录>

# 重算并核验冻结的确证数字，同时重新生成图与表
.\.venv\Scripts\python.exe scripts\render_formal_results_v03.py
.\.venv\Scripts\python.exe scripts\render_formal_results_v03.py --check-only
```

关于受限环境：272 项测试中有 8 项使用 Python `tempfile`，在限制写入的沙箱中会被阻止；
在正常环境下它们会通过。在正式结果冻结之前，完整测试套件是全部通过的。

## 快速开始

请使用独立的 Python 3.11–3.13 环境。Windows 上 `start.ps1` 会选择合适的解释器、创建
`.venv`、安装依赖、运行测试、做一次 smoke 检查，然后跑 100 次 pilot：

```powershell
cd <你的仓库目录>
.\start.ps1
```

也可以手动执行：

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[sim]"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m cornercaselab doctor --smoke
.\.venv\Scripts\python.exe -m cornercaselab run --episodes 100 --seed 20261003 --out runs/pilot01
```

Linux 使用相同命令，把解释器换成 `.venv/bin/python`。直接仿真依赖固定为 HighwayEnv 1.10.2 与
Gymnasium 1.2.2，这是固定的接口目标，不代表它们是最新版本。

重放与局部复测仍然可用（它们是 v0.1 阶段的构建块，不是正式对比）：

```powershell
.\.venv\Scripts\python.exe -m cornercaselab replay runs/pilot01/cases/000000.json --gif runs/case0.gif
.\.venv\Scripts\python.exe -m cornercaselab confirm runs/pilot01/cases/000000.json --repeats 20 --out runs/confirm01
```

`000000.json` 只是第一个执行的案例，**不保证**发生碰撞。严格重放检查的是可复现性，不是事故概率
的证据。复测会额外消耗仿真次数，**不是免费的**。

## 仓库结构

```text
cornercaselab/          核心引擎：预算记账、候选管理、实验调度、checkpoint、
                        scoring、analysis、冻结协议校验
configs/                冻结配置：eval_v03_formal.json
scripts/                run_v03_formal.py、score_v03.py、render_formal_results_v03.py、
                        v0.3 开发驱动、smoke 驱动
docs/                   评估协议、打分协议、研究计划、验证记录、归属与相关工作
reports/formal_v03/     已发布结果：图（PNG+PDF）、表（CSV）、raw_sny_by_seed.csv、
                        数据冻结清单、结果叙述
tests/                  272 项单元/集成测试，含协议与打分测试
runs/                   运行输出（已被 gitignore —— 原始正式数据不随仓库分发）
```

## 局限

1. **SNY 是代理指标，不是真值** —— 不是根因数、不是碰撞概率、不是真实 failure region 数量。
2. **20 个配对种子是冻结协议规定的规模**，不是功效分析结果；区间描述的是这 20 次运行的不确定性，
   而不是所有可能种子的不确定性。
3. **提名是方法的一部分**，三种方法掌握的信息不同；比较的是端到端方法，而不是单独比较提名规则。
4. **设计存在混杂** —— 方法身份同时捆绑提名规则、预算划分与信息集。没有运行隔离「确认」的消融。
5. **仅限仿真。** 结果来自本仓库的简化仿真器与控制器，不能推断真实车辆或真实交通。
6. **Adaptive 受上限约束。** Adaptive 在全部 20 个种子上都达到冻结的 175 次内部确认上限，
   因此它的行为有一部分由冻结参数决定，而不是由自身停止逻辑决定。
7. **空间去重本次未起作用**（处处 `passed == SNY`），所以在本数据集中 SNY 与通过候选数在数值上
   可以互换。
8. 冻结的边界、扰动尺度、控制器与审计设计；32 位推导种子；每个候选只做一次审计抽样、不补跑。

## 开发历史

更早的里程碑已被上面的 v0.3 结果取代，仅作为背景保留：

* **v0.1** —— 最初的测量基础设施：六参数高速/匝道汇入场景适配器、两个固定规则观测型驾驶策略、
  搜索与仿真分离的随机种子、逐案例 JSON 与 append-only ledger、带环境与源码指纹的严格重放、
  固定样本量的局部扰动复测。当时还没有任何原创方法对比，准备环境也尚未验证真实 HighwayEnv 运行。
* **v0.2** —— 统一预算记账、带 interrupted episode 封存的 checkpoint/续跑、可复现性元数据、
  种子流碰撞防护。
* **v0.3** —— 稳定性判据、空间 SNY 打分、冻结正式协议，以及本文报告的完整正式评估。

## 归属与相关工作

HighwayEnv 提供道路几何、车辆动力学、背景 IDM 行为、动作实现与渲染；Gymnasium 提供环境接口；
NumPy 提供数值功能；Pillow 提供可选 GIF 输出；matplotlib 供结果图渲染脚本使用。这些依赖是安装的，
不随仓库打包，并各自保留其许可证。详见
[`docs/ATTRIBUTION.md`](docs/ATTRIBUTION.md) 与
[`docs/RELATED_WORK.md`](docs/RELATED_WORK.md)。本仓库在 AI 作为工具的协助下准备；来源与版权记录见
[`docs/PROVENANCE.md`](docs/PROVENANCE.md)，披露声明见归属文档。

## 许可证

CornerCaseLab 自身的代码与文档采用 **Apache License, Version 2.0**（见
[`LICENSE`](LICENSE)）。第三方依赖保留各自的许可证，不因本项目而重新授权。依赖归属见
[`docs/ATTRIBUTION.md`](docs/ATTRIBUTION.md)，来源与版权记录见
[`docs/PROVENANCE.md`](docs/PROVENANCE.md)。

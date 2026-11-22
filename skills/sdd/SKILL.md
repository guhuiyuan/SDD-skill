---
name: sdd
description: |
  规范驱动开发(SDD)助手:把需求、架构、任务拆成三份可评审的规范文档
  (proposal.md / design.md / tasks.md),再按规范执行代码生成与整体验收。
  当用户要"先写规范再写代码"时使用——整理需求、写需求、划范围、定验收标准、
  定架构、做技术选型、写 ADR、划模块、设计接口、拆任务、排期、
  按规范实现、照规范写代码、验收、交付、给老项目补规范;也支持直接 /sdd 开始。
  产出固定放在项目根的 specs/<feature>/ 下(三份文档)+ acceptance-report.md,
  辅助脚本在 ~/.claude/skills/sdd/scripts/(init_specs / validate_specs /
  update_claude_md / verify_acceptance / export_openspec / install_hook);
  带一个 PreToolUse hook,在 auto 模式的安全分类器不可用时让命令照常执行。
  仅在项目已启用 SDD(项目 CLAUDE.md 含 SDD 规范区块,或 specs/ 下已有规范目录),
  或用户明确要求走规范流程时使用;普通编码请求不要触发。
allowed-tools: |
  Read, Write, Edit, Glob, Grep, AskUserQuestion, Task, Agent,
  Bash(python:*), Bash(python3:*), Bash(py:*), Bash(pytest:*), Bash(uv:*),
  Bash(pip:*), Bash(poetry:*), Bash(node:*), Bash(npm:*), Bash(npx:*),
  Bash(cargo:*), Bash(go:*), Bash(make:*), Bash(mvn:*), Bash(gradle:*),
  Bash(git:*)
---

# SDD 规范驱动开发

## 这个技能解决什么问题

vibe coding 的失败模式不是"AI 写得慢",而是**没有可以被检验的决策依据**:

- 需求边界靠默契 → AI 顺手加了没要求的功能,评审时才发现
- 技术选型靠默认 → 选型隐含在代码里,没人知道当初为什么这么选,更没人能安全地改
- 验收靠"能跑" → 跑通了不等于做对了,边界场景是 bug 的重灾区

SDD 把这三件事外化成三份可评审、可版本化、可 diff 的文档:
`specs/<feature>/proposal.md`、`design.md`、`tasks.md`。

**规范是唯一事实源,代码是规范的衍生物。**

## 核心原则

1. **规范最大** —— 代码必须能从规范推导出来。反过来不成立:一旦发现代码与规范冲突,
   除非走逆向修改流程把规范改掉,否则以规范为准。
2. **人类决策,AI 执行与辅助决策** —— 目标、范围边界、技术选型、破坏性变更、验收结论、
   任何 git 提交,都由人拍板。AI 负责提问、草拟、枚举方案、校验、编码、修 bug。
   AI 的价值在于**把选项和代价摆清楚**,而不是替人做决定。
3. **规范与代码在 git 中共同演进** —— 三份文档和代码在同一个仓库、同一条提交历史里。
   规范的修改留下 diff,代码的修改能追溯到具体的 Task。
4. **流程不是死的** —— 五个阶段可以单独进入,也可以中途改主意。

## 项目根与 feature 名

规范和 CLAUDE.md 都必须落在**项目根**下,不能落在当前工作目录的偶然位置——
否则脚本找不到文件、路径写进提交历史后也无法追溯。

- **项目根**:用户当前工作目录;若它在 git 仓库内,取仓库根
  (`git rev-parse --show-toplevel`)。所有脚本都显式传 `--root <项目根>`,
  不要依赖默认的当前目录。
- **feature 名**:`specs/` 下的目录名,kebab-case 小写(如 `user-auth`、`ticket-system`)。
  一个 feature 一个目录,内含三份规范文档。
- **先跟用户确认 feature 名再动手**:这个名字会进 commit message、git tag 和
  CLAUDE.md 里的路径,事后改名要连带改一堆引用。

## 五阶段与三份文档

| 阶段 | 产出 | 版本节点 | 详细流程 |
|---|---|---|---|
| 1 定义需求 | `proposal.md` | v0.1 | references/phase1-proposal.md |
| 2 架构设计 | `design.md` | v0.5 | references/phase2-design.md |
| 3 任务拆解 | `tasks.md` | v0.5 | references/phase3-tasks.md |
| 4 代码生成 | 代码 + `tasks.md` 勾选 | — | references/phase4-implement.md |
| 5 验证与交付 | `acceptance-report.md` | v1.0 | references/phase5-verify.md |

**覆盖范围**:默认**一个项目一套基线规范**,模块边界画在 `design.md` §1.1 的模块划分表里,
不拆成多个 feature 目录 —— 拆分的代价是规范层出现多真相源(同一数字在两份规范里各钉
一次、同一份评测集被重新分类两遍、ADR 编号各自从 001 起)。判断依据见
`references/phase1-proposal.md` 的「覆盖范围」决策。

版本号含义:`v0.1` = 最小可行(需求已定义);`v0.5` = 基线规范(三份齐备);
`v1.0` = 首版完成(实现完成且全部验收通过);之后 `v1.x` 为增量、`v2.0` 为不兼容变更。

跨阶段约定(ADR 规则、编号规范、追溯矩阵、反模式)见 `references/conventions.md`。

## 第一步:判定模式与阶段

### 0. 先判定规范模式

读 `references/modes.md`,判定是 **build**(定义要做什么)还是 **as-is**(固化现在是什么)。
两种模式的 §1.2 目标写法、§3 的 AC 含义、§2 的范围表述完全不同,用错模式会让整份文档的
抽象层漂移。判定结果要告诉用户,不要默认。

### a. 看项目状态

**先跑校验器问它当前在哪个阶段**——不要只看文件在不在。骨架文件也存在,但内容没填,
那时候应该回到该文档对应的阶段,而不是跳到 Phase 4 去写代码。

```bash
python ${CLAUDE_SKILL_DIR}/scripts/validate_specs.py --feature <feature> --phase
```

输出形如:

```
当前阶段:Phase 1 — 定义需求
  阻塞项 29 条,先修这些再往下走:
    - 第 16 行仍有未填写的占位符 …
```

判据是**内容完成度**:哪份文档还有错误就回到哪一阶段;三份都干净了,再看 Task 勾选进度
决定是 Phase 4(还有未完成的 Task)还是 Phase 5(全部完成)。

没有 `specs/` 目录 → Phase 1(先和用户确认 feature 名)。

### b. 按用户意图的语义推断

用户很少说"进入 Phase 2",而是说"帮我把架构定一下"。按语义而非字面匹配:

| 用户可能说 | 阶段 |
|---|---|
| 需求、想法、功能、要做个 X、背景、目标、范围、做什么、不做什么、验收标准 | 1 proposal |
| 架构、怎么设计、技术选型、用什么框架、ADR、模块怎么分、数据结构、接口、API 契约、性能约束、安全约束 | 2 design |
| 任务、拆解、拆细、排期、分几步、TODO 清单 | 3 tasks |
| 实现、写代码、开工、落地、做出来、跑起来 | 4 implement |
| 验收、测试、整体检查、能不能上线、交付 | 5 verify |
| 补规范、给老项目加文档、规范化一下 | 最早缺失的那一阶段 |
| SDD / spec-driven / 走流程 / 规范驱动 | 从当前状态所处阶段继续 |

### c. 路由规则

- 意图与项目状态**一致** → 直接进入该阶段的 references 文件
- 意图与项目状态**冲突** → **先说明冲突,再用 AskUserQuestion 给候选**。例如项目已有 tasks
  完成到 T-007,用户却说"写需求":选项给「补一个新 feature 的 proposal / 修改现有 proposal /
  直接继续实现」。不擅自跳转。
- 命中**多个候选** → AskUserQuestion 澄清,选项就是各候选阶段,每项附一句当前项目状态
- **完全无匹配** → 给出五个阶段的选项让用户挑,并说明当前处于哪个阶段
- **推断结果必须明示**:进入某阶段时写清「识别到你要的是 X 阶段(依据你说的『YY』),项目当前
  状态是 Z。判断有误请纠正」。让用户能一键改判,而不是默默走错。

## 提问纪律

这是 SDD 体验好坏的分水岭。开放式追问("你想要什么?")让用户从零口述;
用户在选项上做增量修正的成本低得多。

- 所有澄清一律用 **AskUserQuestion**,不用开放式自由文本提问
- 每个问题给 **2–4 个实质差异的选项**(不是同义改写),每项附**一句说明**,
  推荐项放首位并标注 "(推荐)"
- 提问前先给**背景与默认值**:基于已读到的文档、代码、上下文,先写出推断出的默认答案和依据,
  让用户改选项而不是从头说
- 一次最多 4 个问题,按「对下游的影响程度」排序——最影响架构的先问
- 不追问能自行查证的信息:先查代码、官方文档、已有规范,再问
- 需要人**拍板**而非澄清的场景(选型、范围边界、验收结论)同样用选项化提问,
  选项文案直接写清各自的代价

示例(Phase 1 提问,注意每个选项都带信息量,而不只是名词):

> 目标用户是谁?
> - **个人开发者 / 自用工具 (推荐)** —— 单用户、数据量小,无需账号体系与权限模型
> - **小团队内部使用 (5–20 人)** —— 需要基础登录与共享数据,不做审批流
> - **公网多租户 SaaS** —— 必须有租户隔离、计费、审计日志

## 人机分工

| 谁 | 决定什么 |
|---|---|
| 人(必须拍板) | 目标、范围边界与"不做什么"、技术选型、破坏性变更、验收结论、任何 git 提交 |
| AI(可自主) | 提问顺序、文档草稿、方案枚举、机器校验、修 bug、写代码、更新 tasks 勾选 |

AI 禁止:擅自扩大范围;增加 proposal 里没有的功能;删除或改写历史 ADR;跳过验收;
未经确认就提交代码或规范。遇到冲突时**停下来问,而不是猜**。

## 通用铁律

1. `proposal.md` §2.2「不做什么」至少 3 条 —— 明确排除什么,某种程度上比做什么更重要
2. 每条验收标准(AC)都能落成一个可自动测试的用例:可观测 + 二值化 + 有验证命令
3. ADR 只追加不删除。废弃写"已被 ADR-xxx 取代",原文保留
4. 需求/架构变更自上而下:proposal → design → tasks → 代码
5. bug 暴露设计缺陷时逆流而上:先改 design.md(及受影响的 tasks),经用户确认,再改代码,
   并在 changelog 留痕
6. 每次修改规范后都跑 `scripts/validate_specs.py`
7. 每条 AC 至少被一个 Task 覆盖;Task 的依赖构成 DAG,不得有环
8. 修订已有规范时给变动的 AC 加 delta 标记(`<!-- ADDED -->` / `<!-- MODIFIED -->` /
   `<!-- REMOVED -->`),让下游影响面可计算
9. **一个行为只在一处定义** —— `proposal §3` 是行为契约的唯一事实源;ADR 只写「为什么」,
   提到行为时用「对应 AC」引用;证据小节只标注支撑强度;缺口清单只记偏离
10. 可被配置覆盖的常量写「默认 X + 覆盖方式」,不写死字面值

## 反模式对照

| vibe coding 症状 | SDD 修正 |
|---|---|
| "先做个 demo 看看" | 至少写清 §2.2 三条"不做什么" + 三条 AC,再写代码 |
| 一句话需求就直接开写 | 先过 Phase 1 的 Gate 清单 |
| "技术选型你定就行" | 仍然给 2–3 个选项 + 代价让人确认;默认也要确认 |
| 任务拆成"写完所有模块" | 拆到"一组文件 + 一条验证命令"的粒度 |
| 代码能跑就算过 | 跑 AC 回放矩阵,四维度验收 |
| 规范过期了没人管 | 逆向修改流程:改规范 → 用户确认 → 再改代码 |
| 规范和代码悄悄分叉 | 提交前跑 `validate_specs.py`;代码提交必须能追溯到 Task |

## 受限环境下的降级与解锁路径

有些环境(代理端点、自建网关、只读沙箱)里 **auto 模式的 bash 审批会整体失效**。
Claude Code 的 auto 模式靠**服务端安全分类器**代替用户回答权限问题;分类器不可达时,
所有需要它判断的命令**被直接拒绝**——auto 模式没有"回退到问用户"的路径。

报错长这样:

```
Auto mode is unavailable: its safety check does not run for this session, so Bash was not run.
Other actions that need the check will be denied too, and retrying won't help.
```

**不要重试。** 这条拒绝不是"这次没过、下次再来"的意思。

### 0. 先装 hook —— auto 模式下唯一能根治的办法

本技能带了一个 PreToolUse hook,它跑在**任何权限模式检查之前**,返回 `allow`
即可跳过权限检查 —— **纯本地机制,不经过服务端分类器**。装上之后 auto 模式下
**跑命令**与**写项目内文件**都照常可用。

> **hook 也管「派发子 agent」这一步**(第三组 matcher:`Agent|Task`)。
> 放行的只是**派发动作**;子 agent 继承会话级设置,**内部**跑命令仍走这份
> hook 白名单(Bash 只放白名单命令、写文件只放项目内),其他工具仍要服务端分类器。
> 没有这一步,auto 模式下分类器不可达时连派发都会被拒,SDD 的并行实现没法用。

一次性安装(在项目根目录跑):

```bash
python ${CLAUDE_SKILL_DIR}/scripts/install_hook.py --root . --feature <feature>
```

它写三样东西:hook 本体、可编辑的白名单、`.claude/settings.json` 的
`hooks.PreToolUse` 条目。**只动这一个键**,不碰你已有的 `permissions`;
重复运行幂等。

> **第一次安装需要用户手动跑一次。** `.claude/` 是受保护目录,AI 代跑会被权限门拦下。
> 装完**通常不用重开会话**(实测 harness 会重读 settings.json;没生效再新开一个)。
> 之后再升级 hook 可以交给 AI —— `install_hook.py` 本身就在 hook 的放行范围内。
>
> 不想写项目文件也可以用 `/hooks` 命令手动注册 —— 命令里填 hook 的**绝对路径**
> (`python "<技能目录>/hooks/sdd_bash_allow.py"`)。注意 `${CLAUDE_SKILL_DIR}` 只在
> 本技能的内容里会被替换,手工填的命令里它不会生效。

hook 放行:

- SDD 自己的脚本(python + `skills/sdd/scripts/*.py` 或 `.claude/sdd/*.py`;
  要求路径**整段**命中且落在可信根下)
- git 的安全子命令(status/diff/log/show/rev-parse/branch/add/commit)
- 常见测试命令(pytest / npm test / cargo test / go test)与白名单文件里的额外命令
- **只读命令**(ls / cat / head / grep / find / diff …;路径必须在项目目录内)
- **写文件**(Write / Edit / MultiEdit / NotebookEdit):目标在项目目录内;
  路径里任何一段叫 `.claude` 或 `.git` 都不放行(留给人手动改)

**不放行**:rm / curl / wget / sudo 等危险可执行文件(即使写进白名单也不放行)、
**shell 与脚本解释器**(sh / bash / cmd / powershell / perl / node / npx /
osascript …)与包装器(env / xargs)—— 它们能执行任意命令、`find -exec`、
git push/reset/clean 与任何改变 git 行为的前置选项(`-c` 能定义 alias)、
任意 python(`-c` / `-m 任意模块`)、太松的白名单模式、
`sed`(脚本里能藏写文件 / 读文件 / 执行;用 Read / Grep 工具代替)、
只读命令里**带环境变量**的(`echo $API_KEY` —— 变量的值会被打进 transcript)、
**危险的环境变量前缀**(`PATH=…` / `LD_PRELOAD=…` / `GIT_DIR=…` / `HOME=…`
—— 能换掉命令查找路径、注入代码、写到别的仓库;普通变量名照常放行),
**文件重定向**(`> >> <`)、**后台运行**(`&`)、以及**任何位置**的命令替换
(`$( )` 与反引号,包括藏在 `cd <路径> &&` 的参数里)。

**复合命令按段判定**:`&&` `||` `;` `|` 与换行会按 shell 习惯在引号外拆段,
**每段单独判定,全过才整条放行** —— `cd X && pytest`、`sleep 5 && cd X && pytest`、
`cat f | head -20`、`git status && git diff` 都放行;而 `python x.py && rm -rf /`
不放行(rm 那一段过不了)。只读命令的路径参数必须落在项目目录内。

它**不能覆盖** `permissions.deny` / `ask` 规则(deny/ask 优先),所以不会削弱
已有的安全规则。安装器会逐条打印放进白名单的命令供你核对;复合命令按段拆开
逐段收,只要有一段不安全,整条都不收。

### 1. auto 模式契约(不切模式也能把活干完)

auto 模式的「要不要问用户」在**服务端**判定;代理端点不透传那个调用 → 不可达 →
**所有轮到它判断的命令一律硬拒**(auto 模式没有「退回去问用户」的路径)。
本地唯一能放行的通道是**上面那个 PreToolUse hook** —— 所以整个技能的工作流
必须按「只依赖本地可判定的动作」来设计。

**契约的四条**(违反了才会被拒,所以是硬约束):

1. **规范是白名单的来源**。每个 Task 的「验证命令」必须能进白名单 ——
   `validate_specs.py` 现在会查(报「验证命令在 auto 模式下跑不了」),
   写规范时就要避开:重定向(`> >> <`)、后台运行(`&`)、命令替换(`$( )` /
   反引号)、shell 与解释器(`sh` / `bash` / `cmd` / `node` / `npx` …)、
   任意 python(`python -c` / `python -m 任意模块`)。
2. **Phase 0 装 hook 时带 `--feature <feature>`**,把当前 feature 的验证命令
   一次性登记进项目白名单(安装器会把复合命令按段拆开逐段收)。
   项目**常跑**的命令(构建 / lint / 格式化 / 启动)写在 tasks.md 的
   `## 常用命令` 小节里,装 hook 时同样一次性登记 —— 声明过的,agent 不用
   每次都问用户;没声明的照旧要问(声明是**授权**,不是猜测):

   ```markdown
   ## 常用命令

   - `npm run build`
   - `cargo clippy --manifest-path src/app/src-tauri/Cargo.toml`
   ```

   `validate_specs.py` 会检查这些命令在 auto 模式下跑不跑得了(跑不了的会报错)。
3. **命令被拒时不要立刻问用户,先自己走完本地通道**:
   1. `python ${CLAUDE_SKILL_DIR}/scripts/hook_probe.py "<原命令>"`
      → 看 `allowed` 是 true 还是 false(它只判定,不执行命令)。
   2. `allowed=false`、而这条命令**出现在规范里**(某个 Task 的验证命令,
      或「常用命令」小节里)→ 登记后重试,不用切模式:
      `python ${CLAUDE_SKILL_DIR}/scripts/install_hook.py --root . --extra "<原命令>"`
      (安装器本身就在白名单里,而且会逐条打印它登记了什么)。
   3. `allowed=false`、而这条命令**不在规范里** → 问用户(见下面的降级流程)。
      —— **不要自己登记**:白名单是授权,不是便利。
4. **联网检索走 MCP,不要用 WebFetch / WebSearch** —— 那两个工具在 auto 模式下
   也要服务端分类器,必然被拒;具体的 MCP 工具(如 `mcp__MiniMax__web_search`)
   走的是白名单里的精确规则,不受「宽泛规则被丢弃」那条策略影响。

### 1.1 降级流程(登记与问用户都解决不了时)

**任何**命令被拒 —— 不只是验证命令,调查/运维命令被拒同样适用 —— 尤其看到
`Auto mode is unavailable: its safety check does not run for this session`
这条报错时,说明本会话处在受限环境(服务端安全分类器不可达)。按顺序:

1. **不重试**。
2. 先按上面「契约」第 3 条走一遍本地通道(探针 → 登记 → 重试)。
3. 还是不行 → **用 AskUserQuestion 问用户**——不要只在正文里贴命令。问题里
   必须带:**命令原文** + **这条命令要干什么** + **被拒的原因**。三个选项:

   | 选项 | 用户做什么 | AI 接着做什么 |
   |---|---|---|
   | **我切到非 auto 模式,你再重试 (推荐)** | 切到 `acceptEdits` / `default`(VS Code 权限模式选择器,或 `/permissions`)。切换后 bash 走**本地权限弹窗**,点允许即可执行 | 重试同一条命令 |
   | **我手动跑这条命令,输出贴给你** | 在终端里跑,把输出贴回来(AskUserQuestion 的「Other」可直接粘贴) | 依据输出判定,**不再调 Bash** |
   | **跳过这条,标记为待人工校验** | 选它 | 继续本阶段其余工作;命令记进阶段小结的「待人工执行的命令」;相关 Gate 结论标 **「未验证」** |

   - 同一条命令不重复问;一个阶段里多条被拒命令**合并成一次** AskUserQuestion。
   - 用户选「切模式」后重试仍被拒 → 明确告诉用户"切模式也没解决,这是端点侧的
     持续性问题",引导到后两个选项。
   - **AI 不得声称"已授予权限"**——AskUserQuestion 不改权限引擎,auto 模式下
     没有这条通道。
4. **继续本阶段其余工作**,把跑不了的命令记进阶段小结的「待人工执行的命令」清单。

### 2. 硬依赖:Phase 4 的验证命令

Phase 4 每个 Task 的「验证命令」是闭环的前提,**跑不了就停在原地**:
不推进 Task、不默认通过、不把 Gate 结论写成「通过」。
默认通过是最危险的降级——它会让整套闭环失效,而且是在没人察觉的情况下。

### 3. Phase 1–3 与 Phase 5 的校验不依赖 bash

用 Read 读出规范文档,对照 `references/phaseN-*.md` 里的 Gate 清单逐条核对,
把每条的结论(通过 / 不通过 / 不适用 / **未验证**)和依据报给用户。
脚本走法更稳(不会漏看),人工走法在受限环境里可用——**两者等价**。

### 4. 工具分工

| 用途 | 用什么 |
|---|---|
| 写规范、读代码 | Read / Write / Edit / Grep / Glob(本地文件工具;auto 模式对它们有内建放行,hook 的路径判定是兜底) |
| 跑校验脚本 | Bash(python …),或人工核对 |
| 跑项目测试 | Bash(Phase 4 硬依赖) |
| git 操作 | Bash(git …) |

## 资源索引

**references/**(按需读,不要一次全读)
- `modes.md` —— **规范模式:build 与 as-is**。写任何内容之前先读它,模式决定 §1.2 目标怎么写、§3 的 AC 描述什么
- `phase1-proposal.md` ~ `phase5-verify.md` —— 各阶段执行剧本(目标 / 输入 / 动作序列 / Gate / 常见坑)
- `conventions.md` —— ADR 规则、版本号、编号规范、追溯矩阵、反模式(跨阶段,首次进入时读)

**assets/templates/**(写文档时复制骨架)
- `proposal.md` / `design.md` / `tasks.md` —— 三份规范文档骨架
- `acceptance-report.md` —— 阶段 5 验收报告骨架
- `claude-md-block.md` —— 注入项目 CLAUDE.md 的区块

**scripts/**(确定性操作,直接执行)
- `init_specs.py <feature>` —— 创建 `specs/<feature>/` 并铺好三份骨架
- `validate_specs.py --feature <feature>` —— 校验规范完整性、AC 可测试性、DAG 无环、追溯闭合、delta 影响面
- `update_claude_md.py --feature <feature>` —— 把规范路径注入项目 CLAUDE.md(marker 包裹,幂等)
- `verify_acceptance.py --feature <feature>` —— 验收报告行为自检:防止漏验却宣布通过
- `export_openspec.py --feature <feature>` —— 把规范导出为 OpenSpec 目录结构(生态互操作)
- `install_hook.py --root . [--feature <feature>]` —— 装 PreToolUse hook(auto 模式下让 bash 可用)
- `hook_probe.py "<命令>"` —— 问 hook「这条命令会不会被放行」,输出 JSON;
  命令被拒却看不出原因时用它定位(`--list` 打印当前白名单)

脚本用 Python 3 标准库实现,无第三方依赖。命令按 `python ${CLAUDE_SKILL_DIR}/scripts/<脚本名>` 调用;
环境里是 `python3` 或 `py` 时替换即可。

**hooks/**
- `sdd_bash_allow.py` —— PreToolUse hook:放行 SDD 自己的脚本、git 安全子命令、常见测试命令
- `sdd-hook-allow.json` —— hook 的可编辑白名单(装到项目的 `.claude/hooks/` 下)

> 命令被拒、但看不出为什么时,先跑 `hook_probe.py "<原命令>"` 看结论 ——
> 它输出 `{"allowed": false, "reason": null}` 就说明没命中白名单,
> 把它写进 `.claude/hooks/sdd-hook-allow.json` 的 `extra` 即可(危险命令除外)。

## 收尾:注入 CLAUDE.md

规范写完后必须让**每一个新会话**都知道要遵守它们:

```bash
python ${CLAUDE_SKILL_DIR}/scripts/update_claude_md.py --feature <feature>
```

它会在项目 `CLAUDE.md` 里维护一段 marker 包裹的区块,内容为规范文件路径 +
「本项目开发工作必须遵循以下规范文档」+ 当前阶段 + 未完成 Task 数。已存在则只更新区块内容。

区块是**项目级索引**:它会列出 `specs/` 下的全部 feature,所以在已有 feature 的项目里新增
第二个 feature 后,再跑一次本脚本不会把第一个 feature 的路径挤掉。区块之外的用户内容不会被改动。

这样规范就不只是文档,而是**每个会话的默认约束**。

## 与 /git skill 的衔接

本技能假设已安装用户级 `git` skill。所有提交遵循:

- **提交前一律 AskUserQuestion 确认**(规范文档与代码一视同仁)
- 规范提交前缀 `docs(spec): ...`,例:`docs(spec): 完成 user-auth proposal v0.1`
- 代码提交前缀 `feat(...)` / `fix(...)`
- 规范类提交前先跑 `validate_specs.py`;代码类提交前先跑该 Task 的验证命令
- 校验不通过就不提交

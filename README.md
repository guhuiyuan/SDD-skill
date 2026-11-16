# SDD Skill — 规范驱动开发

面向 Claude Code 的**规范驱动开发(Specification-Driven Development)**工作流技能。

把 vibe coding 换掉。vibe coding 的失败模式不是"AI 写得慢",而是**没有可以被检验的决策依据**:
需求边界靠默契、技术选型靠默认、验收靠"能跑"。SDD 把这三件事外化成三份可评审、可版本化、
可 diff 的文档——`proposal.md` / `design.md` / `tasks.md`。

> **规范是唯一事实源,代码是规范的衍生物。**

---

## 核心思想

| 原则 | 含义 |
|---|---|
| 规范最大 | 代码必须能从规范推导出来。代码与规范冲突时,以规范为准,或先改规范 |
| 三份文档覆盖全生命周期 | proposal 定义需求 → design 定架构 → tasks 拆执行 → 代码 → 验收 |
| 人类决策,AI 执行与辅助决策 | 目标、范围、选型、验收、提交由人拍板;AI 负责提问、枚举方案、校验、编码 |
| 规范与代码在 git 中共同演进 | 同一仓库、同一提交历史,规范的修改留下 diff,代码能追溯到 Task |

五个阶段**不是死的**——可以单独进入某一阶段,也可以中途改主意。

| 阶段 | 产出 | 版本 |
|---|---|---|
| 1 定义需求 | `proposal.md` | v0.1 |
| 2 架构设计 | `design.md` | v0.5 |
| 3 任务拆解 | `tasks.md` | v0.5 |
| 4 代码生成 | 代码 + tasks 勾选 | — |
| 5 验证与交付 | `acceptance-report.md` | v1.0 |

---

## 安装

### 方式一:当作 plugin(推荐,适合分发)

```bash
git clone <this-repo>
claude --plugin-dir /path/to/SDD-skill
```

### 方式二:用户级 skill(所有项目可用)

```bash
cp -r skills/sdd ~/.claude/skills/sdd
```

Windows(Git Bash):

```bash
cp -r skills/sdd "$HOME/.claude/skills/sdd"
```

**想保持单一来源**(仓库改了用户级立刻跟着变,不用重新拷贝),把用户级目录做成
指向仓库的**符号链接 / 目录联接**:

```bash
mkdir -p ~/.claude/skills
ln -s "$PWD/skills/sdd" ~/.claude/skills/sdd          # Git Bash / macOS / Linux
# Windows(cmd,需管理员或开发者模式):
# mklink /J "%USERPROFILE%\.claude\skills\sdd" "%CD%\skills\sdd"
```

> 目标**已经是**链接时不要再对它 `cp -r` —— 那是把源目录拷进自己
> (报 "cannot copy a directory into itself"),无害但没意义。

### 方式三:项目级 skill(随仓库分发,团队共享)

```bash
cp -r skills/sdd <your-project>/.claude/skills/sdd
```

安装后重启 Claude Code(或执行 `/reload-skills`)。

> **技能是全局的,hook 是项目级的。** 技能装到用户级后所有项目都能用 `/sdd`;
> 但每个项目的 auto 模式放行机制要在**那个项目里**各装一次
> (见下文「装 PreToolUse hook」)。

**依赖**:Python 3.7+(仅用标准库,用于三个辅助脚本)。不装也能用——脚本只是可选的校验与脚手架。

### 用代理端点 / 自建网关时的权限配置

Claude Code 的 auto 模式靠**服务端分类器**代替用户回答「这条命令要不要执行」。
如果你的 `ANTHROPIC_BASE_URL` 指向代理或自建网关,而它不透传这个分类器调用,
分类器就不可达 —— 结果是**所有需要它判断的命令都被直接拒绝**,而不是弹窗问你
(auto 模式没有「回退到问用户」的路径)。

报错长这样:

```
Auto mode is unavailable: its safety check does not run for this session, so Bash was not run.
Other actions that need the check will be denied too, and retrying won't help.
```

SDD 流程是 Bash 密集的(6 个 Python 脚本 + git + 每个 Task 一条验证命令),
这个环境对 SDD 的影响最大。**首选解法是装本技能自带的 PreToolUse hook**(下一节),
它纯本地生效,不依赖服务端分类器。

<details>
<summary><b>为什么不能只靠 <code>permissions.allow</code>(实测结论)</b></summary>

常见建议是「把 `Bash(python:*)` 加进 allowlist 就跳过分类器了」—— **实测不成立**。
18 个对照探针的结果:

| 命令 | 匹配到的规则 | 结果 |
|---|---|---|
| `git log --oneline -5` | `Bash(git:*)` | ✅ 放行 |
| `npm run` | `Bash(npm:*)` | ✅ 放行 |
| `git rm -h` | `Bash(git rm *)` | ✅ 放行 |
| `python <脚本> --feature x` | `Bash(python:*)` | ❌ 拦截 |
| `python -c "…"` | `Bash(python:*)` | ❌ 拦截 |
| `python -c '…'` | `Bash(python -c ' *)` | ❌ 拦截 |
| `.venv/Scripts/python.exe -c "…"` | `Bash(.venv/Scripts/python.exe:*)` | ❌ 拦截 |
| `python <脚本> <固定参数>` | **钉死完整 argv 的精确规则** | ✅ 放行 |

规律:**非解释器命令**(git / npm / cargo)的通配规则有效;**python 的任何带通配的
规则都无效**,只有把完整 argv 钉死的精确规则才放行(多一个 `--json` 就不匹配)——
而精确规则覆盖不了参数会变的场景。

已排除的解释:不是会话状态、不是 token 额度、不是命令危险(被送去分类的全是
`git log` / `grep` / `cat` 这类只读命令)、不是脚本在工作区内还是区外、
不是 `cd &&` 复合形式、也不是 `dangerouslyDisableSandbox`。

</details>

不想装 hook 的话,只能用 `/permissions` 切到 auto 以外的模式 —— 非 auto 模式用的是
**本地权限弹窗**,不需要服务端分类器。

> 注意:allowlist 与 skill 自己的「提交前先用 AskUserQuestion 问用户」是两层独立的
> 东西 —— allowlist 放行了 `git commit`,不代表 AI 会不问就提交。

---

### 装 PreToolUse hook(auto 模式下让命令照常执行)

本技能带一个 PreToolUse hook。它跑在**任何权限模式检查之前**,返回 `allow` 即可
跳过权限检查 —— **纯本地机制,不经过服务端分类器**,所以端点透传不了分类器也不影响它。
子 agent 继承的是会话级设置,不是技能的 `allowed-tools`,所以装进
`settings.json` 的 hook 也覆盖得到子 agent **内部**跑的命令。

> **hook 也覆盖「派发子 agent」这一步**(第三组 matcher)。放行的只是**派发动作**;
> 子 agent 继承会话级设置,**内部**跑命令仍走这份 hook 白名单,其他工具仍要服务端分类器。
> 没有这一步,auto 模式下分类器不可达时连派发都会被拒,SDD 的并行实现就没法用。

在项目根目录跑一次:

```bash
python <技能目录>/scripts/install_hook.py --root . --feature <feature>
```

它写三样东西:

| 落点 | 内容 |
|---|---|
| `<项目根>/.claude/hooks/sdd_bash_allow.py` | hook 本体 |
| `<项目根>/.claude/hooks/sdd-hook-allow.json` | 可编辑的白名单 |
| `<项目根>/.claude/settings.json` 的 `hooks.PreToolUse` | 注册条目(**两组** matcher) |

**只动 `hooks.PreToolUse` 这一个键**,不碰你已有的 `permissions`;重复运行幂等。
给了 `--feature` 时,还会从 `specs/<feature>/tasks.md` 的「验证命令」字段提取命令
写进白名单 —— 这样阶段 4 跑测试、子 agent 跑测试都不会再撞权限门。

注册的两组 matcher:

| matcher | 放行依据 |
|---|---|
| `Bash` | 命令白名单(下表) |
| `Write\|Edit\|MultiEdit\|NotebookEdit` | **路径**:目标在项目目录内 |
| `Agent\|Task` | 放行**派发子 agent**这一步;子 agent 内部仍走同一份白名单 |

> **第一次安装需要你手动跑一次**:那时还没有 hook,`.claude/` 这个受保护目录只能你来写。
> 装完之后**升级/重装可以交给 AI** —— `python <技能目录>/scripts/install_hook.py`
> 本身就在 hook 的放行范围内。
>
> 装完**通常不用重开会话** —— 实测 harness 会重读 settings.json(2026-11-16 验证:装完当场
> Edit 就被放行了)。要是没生效,再新开一个会话。
>
> 不想往项目里写文件,也可以用 `/hooks` 命令手动注册 —— 命令里填 hook 的**绝对路径**
> (`python "<技能目录>/hooks/sdd_bash_allow.py"`)。`${CLAUDE_PROJECT_DIR}` 只在
> 本项目的 `.claude` 目录正好是 Claude Code 项目目录时才对(从子目录启动就不对),
> 所以默认用绝对路径;确认无误也可以加 `--portable`。

**放行范围**:SDD 自己的脚本、git 的安全子命令(status/diff/log/show/rev-parse/
branch/add/commit)、常见测试命令(pytest / npm test / cargo test / go test),
白名单文件里的额外命令、**项目目录内的文件写入**(Write / Edit /
MultiEdit / NotebookEdit),以及**派发子 agent**(Agent / Task)。

> 写文件这一组是按**路径**判定的:目标在项目目录内就放行。想收紧就在白名单文件里填
> `"write_paths": ["specs", "src"]`(相对项目根的子路径),留空 = 整个项目目录;
> 写成一堆空串则**一律不放行**(fail-closed)。
>
> **路径里任何一段叫 `.claude` 或 `.git` 都不放行** —— 前者能给自己授权权限,后者能改
> git 的 hook 与配置,这两处留给人手动改。
>
> 补充:auto 模式对写文件本来有内建短路(「若在 acceptEdits 模式下会被放行」),
> hook 的路径判定是**兜底**;分类器不可达时它才真正起作用。

**不放行**:

- rm / curl / wget / sudo / chmod 等危险可执行文件 —— **即使你写进白名单也不放行**
- **shell 与脚本解释器**(sh / bash / cmd / powershell / perl / ruby / node / npx /
  osascript / wscript …)与**包装器**(env / xargs)—— 它们能执行任意命令,
  白名单里写一条 `sh -c "任意命令"` 就是开后门
- `find` 带 `-exec` / `-delete`
- git push / reset / clean,以及任何会改变 git 行为的**前置选项**
  (`-c` 能定义 alias 把"安全子命令"换成任意 shell;`--exec-path` 能换掉子命令查找路径)
- 任意 python(`python -c` / `python -m 任意模块`);解释器必须钉死脚本
- 太松的白名单模式(例如白名单里只写一条 `python` 或一条 `sh`)
- 带 `&&` `;` `|` 等复合操作符的命令 —— 否则 `python 某脚本 && rm -rf /` 会被整条放行
- **任何位置**的命令替换(`$( )` 与反引号),包括藏在 `cd <路径> &&` 的参数里
- **项目目录外**的文件写入,以及路径里带 `.claude` / `.git` 的写入

安装器会**逐条打印**它放进白名单的命令,你可以核对;含复合操作符的验证命令会被
直接跳过并单独列出来。

**它不能覆盖 `permissions.deny` / `ask` 规则** —— Claude Code 里 deny/ask 优先于
hook 的返回值,所以不会削弱你已有的安全规则。删掉 hook 文件与 settings.json 里的
条目即可完全回退。

#### 其他项目也能用

hook **捆绑在 SDD 技能里,但机制是通用的** —— 真正解决的是「auto 模式下 bash 审批
整体失效」,这跟 SDD 没有必然关系。任何项目都可以单独装:

```bash
python <SDD技能目录>/scripts/install_hook.py --root <另一个项目根>
```

**不带 `--feature`** 就是纯通用模式:放行 git 的安全子命令与常见测试命令,
不放行 rm / curl / git push / 任意 python。SDD 专属的那条(放行
`skills/sdd/scripts/*.py`)在非 SDD 项目里匹配不到任何东西,无害。

如果别的技能也想要同一套放行能力,直接复用这个 hook 即可 —— 它读的白名单文件
(`sdd-hook-allow.json`)是可编辑的,加什么放什么。

---

## 使用

### 起步

在项目根目录直接说:

```
/sdd 我想做一个记账工具,记录日常开销
```

或自然语言:`帮我把这个需求写成规范`、`帮我把架构定一下`、`拆一下任务`、`按规范实现`、`验收一下`。

技能会先判断你处在哪个阶段(扫 `specs/` 看进度 + 语义理解你的措辞),然后:

1. 用**选项化提问**补齐缺失信息(每题 2–4 个实质差异的选项 + 推荐项,提问前先给出推断的默认值)
2. 按模板起草对应文档
3. 跑 `validate_specs.py` 机器校验 + Gate 清单自检
4. 展示草稿 → **你确认** → 写入 → git commit(提交前会再问一次)

### 目录约定

```
your-project/
├── specs/
│   ├── user-auth/
│   │   ├── proposal.md
│   │   ├── design.md
│   │   └── tasks.md
│   └── billing/
│       └── ...
├── CLAUDE.md          ← 自动注入规范路径与约束
└── src/
```

**默认一个项目一套基线规范。** 模块边界画在 `design.md` §1.1 的模块划分表里,不拆成
多个 feature 目录 —— 拆分的代价是规范层出现多真相源(同一个数字在两份规范里各钉一次、
同一份评测集被重新分类两遍、ADR 编号各自从 001 起导致 `ADR-001` 在两个目录里指两件事)。

### 两种规范模式

| 模式 | 何时用 | §3 的验收标准描述 |
|---|---|---|
| **build**(默认) | 要新建功能或改造既有功能 | 系统**应该**达到的行为 |
| **as-is**(现状逆向) | 系统已存在且在运行,要把它现在的行为固化成契约 | 系统**现在**必须保持的行为 |

as-is 模式有几条不同的规矩,详见 `skills/sdd/references/modes.md`:

- **§1.2 目标必须写两层** —— 系统层(这个系统要保证什么)+ 规范层(固化成契约)。
  只写一层,全文的抽象层会漂移到「文档」
- **§3 的 AC 是护栏不是进度单** —— 必须能被现有测试二值判定,不能写「文档已产出」
- **区分现状契约与设计意图** —— 实现与注释不符时 AC 钉实现,偏离记进缺口清单
- **采信规则** —— 冲突以实现为准,且要覆盖规范自己的 ADR 文本
- **可覆盖值写「默认 X + 覆盖方式」**,不写死字面值

### 脚本

```bash
# 创建规范骨架
python skills/sdd/scripts/init_specs.py user-auth

# 校验规范(提交前跑;不通过则不提交)
python skills/sdd/scripts/validate_specs.py --feature user-auth

# 把规范路径与约束注入 CLAUDE.md(marker 包裹,幂等)
python skills/sdd/scripts/update_claude_md.py --feature user-auth

# 验收报告行为自检(阶段 5 收尾时跑)
python skills/sdd/scripts/verify_acceptance.py --feature user-auth

# 导出为 OpenSpec 目录结构(生态互操作)
python skills/sdd/scripts/export_openspec.py --feature user-auth

# 装 PreToolUse hook(auto 模式下让 bash 照常执行;需手动跑一次)
python skills/sdd/scripts/install_hook.py --root . --feature user-auth
```

`validate_specs.py` 的检查项:

| 文档 | 检查 |
|---|---|
| proposal | 章节齐全;§2.2「不做什么」≥3 条;每条 AC 可二值化;性能 AC 已量化到阈值;术语与正文一致;AC 编号连续 |
| design | 模块职责表含「不做什么」列;ADR 六要素齐全且编号连续、状态合法;追溯矩阵覆盖全部 AC;假设已承接 |
| tasks | Task 编号连续;标题动词开头;每个 Task 有验证命令;依赖 DAG 无环;追溯矩阵覆盖全部 AC |
| 跨文档 | delta 影响面:标记 MODIFIED/REMOVED 的 AC 列出下游需复核的设计条目与 Task |

`verify_acceptance.py` 的检查项:每条 AC 是否真的验过、结果能否判定、结果与 tasks 勾选状态是否一致、
结论与实测是否相符、性能指标是否实测。

退出码 `0` = 通过,`1` = 有错误。加 `--json` 输出结构化结果。

### Delta 变更语义

规范不是一次写完的。修订时在条目**行尾**加 delta 标记(OpenSpec 同名关键字):

```markdown
- **AC-F-003**: 给定...,系统 SHALL ... <!-- ADDED -->     本轮新增
- **AC-F-002**: 给定...,系统 SHALL ... <!-- MODIFIED -->  本轮修改
- **AC-F-001**: 给定...,系统 SHALL ... <!-- REMOVED -->   本轮移除(原文保留)
```

- `ADDED` 的 AC 必须同步补上 design 追溯条目与 Task,否则校验报错
- `MODIFIED` / `REMOVED` 的 AC,校验器会列出下游受影响的设计条目与 Task,提示复核
- `export_openspec.py` 把它导出成 OpenSpec 的 `## ADDED/MODIFIED/REMOVED Requirements` 三段结构

### 跑测试

改动 `scripts/` 或 `assets/templates/` 之后:

```bash
python tests/run_tests.py
```

回归覆盖脚本的行为:结构校验、AC 可二值化、性能量化、DAG 无环、追溯闭合、
delta 影响面、阶段判定、幂等性、多 feature 共存、OpenSpec 导出、注释里的示例编号
不被当成真实数据、§0 元信息、假设与开放问题分离,as-is(现状逆向)模式的整套规则,
以及 PreToolUse hook 的放行判定(复合操作符、路径穿越、git 子命令白名单)与
安装器的幂等性。
不依赖 pytest。

### 与 `/git` skill 衔接

所有提交**先 AskUserQuestion 确认**——规范文档与代码一视同仁。

- 规范提交:`docs(spec): 完成 user-auth proposal v0.1`
- 代码提交:`feat(user-auth): 实现 token 签发`
- 规范类提交前先跑 `validate_specs.py`;代码类提交前先跑该 Task 的验证命令

---

## 与主流 SDD 工具的关系

本工具不是又一个 SDD 框架,而是**一个装在 Claude Code 里的 SDD 执行纪律**。
下面是它与现有主流方案的定位差异:

| 维度 | 本 skill | GitHub Spec Kit | OpenSpec | SPDD |
|---|---|---|---|---|
| 载体 | Claude Code skill(纯 Markdown + Python 脚本,零依赖) | Python CLI `specify` | Node CLI | 单一提示词 |
| 规范结构 | proposal / design / tasks 三件套 | constitution + spec + plan + tasks | proposal / design / tasks + `specs/` delta | 提示词七要素 |
| 阶段门禁 | 每阶段 Gate 清单 + 机器校验 | constitution 合规门禁 | 无强制门禁 | 无门禁 |
| 变更语义 | ADR 只追加 + delta 标记 + 逆向修改流程 | spec 修订 | ADDED/MODIFIED/REMOVED delta + archive | 提示词↔代码双向同步 |
| 人类介入 | 选项化提问强制拍板(选型/边界/验收/提交) | clarify 阶段结构化问答 | 依赖提议人自律 | 轻 |
| 执行阶段 | 严格闭环 + 子 agent 并行 + 每 Task 带验证命令 | 逐任务执行 | `/opsx:apply` | 双向同步 |
| **独立验收阶段** | **有:功能/接口/架构/性能 + AC 回放矩阵 + 验收报告 + 行为自检脚本** | 无 | 无 | 无 |
| 生态互操作 | 可导出为 OpenSpec 目录结构 | — | 本身即 OpenSpec | — |
| 轻量度 | 无 CLI、无安装,拷贝即用 | 需 Python 3.11+ / uv | 需 Node CLI | 只需提示词 |

**什么时候用哪个**:

- 已经在用 **Spec Kit** 且需要 constitution 级强制 → 用 Spec Kit
- 需要**多方并行提案 + 归档合并** → 用 OpenSpec
- 只想快速把一次对话变成可执行清单 → 用 SPDD
- 想要**人机分工明确、每步有机器可验证的通过判据、并且交付前有正式验收** → 用本 skill

本 skill 与它们**不互斥**:`specs/<feature>/` 的三件套结构与 OpenSpec 的 change 目录同源,
`export_openspec.py` 可把规范导出为 OpenSpec 格式,`openspec/changes/` → `openspec/specs/`
的归档合并也能反向进行。

---

## License

MIT

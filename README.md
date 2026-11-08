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

### 方式三:项目级 skill(随仓库分发,团队共享)

```bash
cp -r skills/sdd <your-project>/.claude/skills/sdd
```

安装后重启 Claude Code(或执行 `/reload-skills`)。

**依赖**:Python 3.7+(仅用标准库,用于三个辅助脚本)。不装也能用——脚本只是可选的校验与脚手架。

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

回归覆盖五个脚本的行为:结构校验、AC 可二值化、性能量化、DAG 无环、追溯闭合、
delta 影响面、阶段判定、幂等性、多 feature 共存、OpenSpec 导出、注释里的示例编号
不被当成真实数据、§0 元信息、假设与开放问题分离,以及 as-is(现状逆向)模式的整套规则。
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

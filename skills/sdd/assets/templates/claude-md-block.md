<!-- SDD:BEGIN -->
## SDD 规范（必须遵循）

本项目的开发工作必须遵循以下规范文档。规范是唯一事实源，代码是规范的衍生物；
若代码与规范冲突，以规范为准，或先走逆向修改流程更新规范。

| 规范 | 路径 | 当前状态 |
|------|------|----------|
| 需求定义 proposal | `specs/<feature>/proposal.md` | v0.1 · AC 12 条 |
| 架构设计 design | `specs/<feature>/design.md` | v0.5 · ADR 4 条 |
| 任务拆解 tasks | `specs/<feature>/tasks.md` | v0.5 · 8/12 完成 |

**当前阶段**:Phase 4 — 代码生成
**下一步**:TASK-009 实现 token 刷新接口(依赖 TASK-007)

规则:
1. 任何代码改动必须对应 `tasks.md` 中的一个 Task，不得做范围外的事。
2. 每个 Task 完成前必须跑通其「验证命令」；未通过不得推进到下游 Task。
3. 不得擅自增加 `proposal.md` §2.1 之外的功能；需要新增时先改规范。
4. 发现规范与实现冲突时，先停下并提出规范修改方案，经确认后再改代码。
5. 提交前跑 `scripts/validate_specs.py`（规范类）或该 Task 的验证命令（代码类）。
<!-- SDD:END -->

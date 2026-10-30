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
"""

import io
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

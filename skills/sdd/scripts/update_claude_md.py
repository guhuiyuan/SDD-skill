#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
update_claude_md.py — 把 SDD 规范注入项目 CLAUDE.md

在 <root>/CLAUDE.md 中维护一段 marker 包裹的区块:

    <!-- SDD:BEGIN -->
    ...规范路径 + 规则...
    <!-- SDD:END -->

已存在 marker 时只替换区块内的内容,区块之外的内容一字不动。CLAUDE.md 不存在时创建。
同一段 marker 只允许出现一次——出现多次时视为配置错误,直接报错。

用法:
    python update_claude_md.py --feature <feature> [--root .] [--all] [--dry-run]

退出码: 0 = 成功; 1 = 用法/配置错误
"""

import argparse
import os
import re
import sys

# Windows 控制台默认可能是 GBK,强制 UTF-8 输出避免中文乱码或编码异常
for _stream in ('stdout', 'stderr'):
    try:
        getattr(sys, _stream).reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

BEGIN = '<!-- SDD:BEGIN -->'
END = '<!-- SDD:END -->'
RULE_BLOCK_RE = re.compile(
    re.escape(BEGIN) + r'(.*?)' + re.escape(END), re.S)

AC_RE = re.compile(r'(?<![A-Za-z0-9])(AC-[FPE]-\d{3})(?![0-9])')
ADR_RE = re.compile(r'(?<![A-Za-z0-9])(ADR-\d{3})(?![0-9])')
TASK_RE = re.compile(r'(?<![A-Za-z0-9])(TASK-\d{3})(?![0-9])')
# Task 标题两种写法: `### TASK-001: 标题`(待办) / `### [x] TASK-001: 标题`(已完成)
TASK_HEADING_RE = re.compile(r'^#{2,4}\s*(?:\[([ xX])\]\s*)?(TASK-\d{3})\s*[:：]?\s*(.*)$')


def parse_tasks(text):
    """解析 tasks.md 的 Task 标题,返回 [(TASK-xxx, 已完成, 标题)] 。"""
    out = []
    for line in text.splitlines():
        m = TASK_HEADING_RE.match(line.strip())
        if m:
            done = (m.group(1) or ' ').lower() == 'x'
            out.append((m.group(2), done, m.group(3).strip()))
    return out


STAGE_ORDER = {
    'Phase 1': 1, 'Phase 2': 2, 'Phase 3': 3, 'Phase 4': 4, 'Phase 5': 5,
}


def stage_order(stage_text):
    """把阶段文本转成可比较的序号;无法识别时返回 99(视为最晚)。"""
    if not stage_text:
        return 99
    for key, val in STAGE_ORDER.items():
        if stage_text.startswith(key):
            return val
    return 99


def read(path):
    if not os.path.isfile(path):
        return ''
    with open(path, 'r', encoding='utf-8') as fh:
        return fh.read()


def meta_value(text, key):
    """从开头的元信息表格里取某个字段的值。"""
    for line in text.splitlines()[:20]:
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        if len(cells) >= 2 and cells[0] == key:
            return cells[1]
    return ''


def build_block(root, feature, features=None):
    """构造 marker 区块内容。features 为 None 时只处理单个 feature。"""
    targets = [feature] if features is None else features

    rows = []
    next_step = ''
    stage = None
    total_tasks = 0
    done_tasks = 0

    for feat in targets:
        base = os.path.join(root, 'specs', feat)
        p_text = read(os.path.join(base, 'proposal.md'))
        d_text = read(os.path.join(base, 'design.md'))
        t_text = read(os.path.join(base, 'tasks.md'))

        p_ver = meta_value(p_text, '版本') or '—'
        d_ver = meta_value(d_text, '版本') or '—'
        t_ver = meta_value(t_text, '版本') or '—'
        ac_count = len(set(AC_RE.findall(p_text)))
        adr_count = len(set(ADR_RE.findall(d_text)))
        tasks = parse_tasks(t_text)
        done = sum(1 for _tid, d, _t in tasks if d)
        total_tasks += len(tasks)
        done_tasks += done

        rows.append('| %s | `specs/%s/proposal.md` | %s · AC %d 条 |'
                    % ('需求定义 proposal', feat, p_ver, ac_count))
        rows.append('| %s | `specs/%s/design.md` | %s · ADR %d 条 |'
                    % ('架构设计 design', feat, d_ver, adr_count))
        rows.append('| %s | `specs/%s/tasks.md` | %s · %d/%d 完成 |'
                    % ('任务拆解 tasks', feat, t_ver, done, len(tasks)))

        if not next_step:
            for tid, is_done, title in tasks:
                if not is_done:
                    # 多 feature 时必须带上 feature 名,否则不知道下一步是谁的
                    label = ('%s · %s %s' % (feat, tid, title)).strip() \
                        if len(targets) > 1 else ('%s %s' % (tid, title)).strip()
                    next_step = label
                    break

        # 每个 feature 各自算阶段,项目整体取**最早**的那个(还有设计没做完就还是 Phase 2)
        if tasks and done < len(tasks):
            feat_stage = 'Phase 4 — 代码生成'
        elif tasks:
            feat_stage = 'Phase 5 — 验证与交付'
        elif d_text:
            feat_stage = 'Phase 3 — 任务拆解'
        elif p_text:
            feat_stage = 'Phase 2 — 架构设计'
        else:
            feat_stage = 'Phase 1 — 定义需求'
        if stage is None or stage_order(feat_stage) < stage_order(stage):
            stage = feat_stage

    lines = []
    lines.append(BEGIN)
    lines.append('## SDD 规范（必须遵循）')
    lines.append('')
    lines.append('本项目的开发工作必须遵循以下规范文档。规范是唯一事实源，代码是规范的衍生物；')
    lines.append('若代码与规范冲突，以规范为准，或先走逆向修改流程更新规范。')
    lines.append('')
    lines.append('| 规范 | 路径 | 当前状态 |')
    lines.append('|------|------|----------|')
    lines.extend(rows)
    lines.append('')
    lines.append('**当前阶段**：%s' % (stage or 'Phase 1 — 定义需求'))
    if next_step:
        lines.append('**下一步**：%s' % next_step)
    elif total_tasks:
        lines.append('**进度**：%d/%d 个 Task 已完成' % (done_tasks, total_tasks))
    lines.append('')
    lines.append('规则：')
    lines.append('1. 任何代码改动必须对应 `tasks.md` 中的一个 Task，不得做范围外的事。')
    lines.append('2. 每个 Task 完成前必须跑通其「验证命令」；未通过不得推进到下游 Task。')
    lines.append('3. 不得擅自增加 `proposal.md` §2.1 之外的功能；需要新增时先改规范。')
    lines.append('4. 发现规范与实现冲突时，先停下并提出规范修改方案，经确认后再改代码。')
    lines.append('5. 提交前跑 `scripts/validate_specs.py`（规范类）或该 Task 的验证命令（代码类）。')
    lines.append(END)
    return '\n'.join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description='把 SDD 规范注入项目 CLAUDE.md')
    ap.add_argument('--feature', help='feature 名(对应 specs/<feature>/)')
    ap.add_argument('--root', default='.', help='项目根目录(默认当前目录)')
    ap.add_argument('--all', action='store_true', help='注入 specs/ 下的全部 feature')
    ap.add_argument('--dry-run', action='store_true', help='只打印将写入的内容')
    args = ap.parse_args(argv)

    if not args.feature and not args.all:
        ap.error('必须给出 --feature 或 --all')

    root = os.path.abspath(args.root)
    specs_dir = os.path.join(root, 'specs')
    if not os.path.isdir(specs_dir):
        sys.stderr.write('找不到目录: %s\n' % specs_dir)
        return 1

    if args.all:
        features = sorted(d for d in os.listdir(specs_dir)
                          if os.path.isdir(os.path.join(specs_dir, d)))
        if not features:
            sys.stderr.write('%s 下没有任何 feature 目录\n' % specs_dir)
            return 1
    else:
        if not os.path.isdir(os.path.join(specs_dir, args.feature)):
            sys.stderr.write('找不到 feature 目录: %s\n'
                             % os.path.join(specs_dir, args.feature))
            return 1
        # CLAUDE.md 里的区块是**项目级索引**,必须列出 specs/ 下的全部 feature。
        # 若只写当前这一个,新增第二个 feature 时会把已有 feature 的路径挤掉,
        # 那份项目的规范就等于从上下文里消失了。
        features = sorted(d for d in os.listdir(specs_dir)
                          if os.path.isdir(os.path.join(specs_dir, d)))
        if not features:
            sys.stderr.write('%s 下没有任何 feature 目录\n' % specs_dir)
            return 1

    claude_md = os.path.join(root, 'CLAUDE.md')
    current = read(claude_md)
    matches = RULE_BLOCK_RE.findall(current)
    if len(matches) > 1:
        sys.stderr.write('CLAUDE.md 中存在多个 %s ... %s 区块,请先手动清理成唯一一份\n'
                         % (BEGIN, END))
        return 1

    block = build_block(root, args.feature, features)

    if args.dry_run:
        print(block)
        print('')
        print('(dry-run,未写入)')
        return 0

    if matches:
        updated = RULE_BLOCK_RE.sub(lambda _m: block, current, count=1)
        action = '更新'
    else:
        sep = '' if (not current or current.endswith('\n\n')) else ('\n' if current.endswith('\n') else '\n\n')
        updated = current + sep + block + '\n'
        action = '新增'

    if updated != current:
        with open(claude_md, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(updated)

    print('%s CLAUDE.md:%s' % (action, claude_md))
    print('已注入 %d 个 feature 的规范路径与约束。' % len(features))
    return 0


if __name__ == '__main__':
    sys.exit(main())

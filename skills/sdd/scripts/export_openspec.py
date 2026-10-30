#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
export_openspec.py — 把 SDD 规范导出为 OpenSpec 目录结构

SDD 的 `specs/<feature>/` 与 OpenSpec 的 `openspec/changes/<change-id>/` 同源:
三件套(proposal / design / tasks)+ 一份行为规格。本脚本做如下映射:

    specs/<feature>/proposal.md  →  openspec/changes/<feature>/proposal.md   (Why / What Changes / Impact)
    specs/<feature>/design.md    →  openspec/changes/<feature>/design.md     (原样保留)
    specs/<feature>/tasks.md     →  openspec/changes/<feature>/tasks.md      (转成 OpenSpec 的复选框编号格式)
    specs/<feature>/proposal.md  →  openspec/changes/<feature>/specs/<capability>/spec.md
                                    （仅本轮 delta:ADDED / MODIFIED / REMOVED)
    specs/<feature>/proposal.md  →  openspec/specs/<capability>/spec.md      (累积的完整当前状态)

delta 标记写在条目行尾,使用 OpenSpec 同名关键字:
    - **AC-F-001**: 给定...,系统 SHALL ... <!-- ADDED -->
    - **AC-F-002**: 给定...,系统 SHALL ... <!-- MODIFIED -->
    - **AC-F-003**: 给定...,系统 SHALL ... <!-- REMOVED -->

用法:
    python export_openspec.py --feature <feature> [--root .] [--capability <name>] [--dry-run]

退出码: 0 = 成功; 1 = 用法/输入错误
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

AC_RE = re.compile(r'(?<![A-Za-z0-9])(AC-([FPE])-(\d{3}))(?![0-9])')
DELTA_RE = re.compile(r'<!--\s*(ADDED|MODIFIED|REMOVED)\s*-->')
TASK_HEADING_RE = re.compile(r'^#{2,4}\s*(?:\[([ xX])\]\s*)?(TASK-\d{3})\s*[:：]?\s*(.*)$')
COMMENT_RE = re.compile(r'<!--.*?-->', re.S)


def strip_comments(text):
    """去掉 HTML 注释,但保留 delta 标记(它们本身也是注释)。

    必须去注释:文档的说明性注释里会写示例编号,不去掉的话示例会被导出成
    真的 OpenSpec Requirement。delta 标记要保留,先替换成 token 再剥注释。
    """
    protected = []

    def protect(m):
        protected.append(m.group(0))
        return '\x00D%d\x00' % (len(protected) - 1)

    text = DELTA_RE.sub(protect, text)
    text = COMMENT_RE.sub(' ', text)
    return re.sub(r'\x00D(\d+)\x00', lambda m: protected[int(m.group(1))], text)

GIVEN_RE = re.compile(
    r'给定\s*(?P<given>[^,，]+?)\s*[,，]\s*当\s*(?P<when>[^,，]+?)\s*时\s*[,，]?\s*'
    r'(?P<subject>[^,，]{0,60}?)\s*(?:SHALL|MUST|SHOULD|应当|应该)\s*(?P<then>.+)$', re.I)
# 退化写法:'在 1000 条数据下,当并发 100 时,列表接口的 P95 SHALL 小于 200ms'
COND_RE = re.compile(
    r'(?:在\s*(?P<given>[^,，]+?)\s*(?:条件下|的情况下)?\s*[,，]\s*)?当\s*(?P<when>[^,，]+?)\s*时\s*[,，]?\s*'
    r'(?P<subject>[^,，]{0,60}?)\s*(?:SHALL|MUST|SHOULD|应当|应该)\s*(?P<then>.+)$', re.I)


def read(path):
    if not os.path.isfile(path):
        return ''
    with open(path, 'r', encoding='utf-8') as fh:
        return fh.read()


def write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(content)


def parse_acs(text):
    """解析 proposal 的验收标准,返回 [{id, kind, desc, delta}] 。"""
    out = []
    text = strip_comments(text)
    for line in text.splitlines():
        m = AC_RE.search(line)
        if not m:
            continue
        ac_id = m.group(1)
        rest = line[m.end():]
        rest = re.sub(r'^\s*\*?\*?\s*[:：]\s*', '', rest)
        rest = re.sub(r'\*\*', '', rest)
        desc = DELTA_RE.sub('', rest).strip()
        delta = DELTA_RE.search(line)
        out.append({
            'id': ac_id,
            'kind': m.group(2),
            'desc': desc,
            'delta': delta.group(1) if delta else '',
        })
    return out


def split_gwt(desc):
    """把 '给定 X,当 Y 时,系统 SHALL Z' 拆成 (given, when, subject, then)。"""
    for regex in (GIVEN_RE, COND_RE):
        m = regex.search(desc)
        if m:
            gd = m.groupdict()
            return ((gd.get('given') or '').strip(),
                    (gd.get('when') or '').strip(),
                    (gd.get('subject') or '').strip(),
                    (gd.get('then') or '').strip())
    # 退化:整体作为 THEN
    return ('', '', '', desc)


def ac_to_requirement(ac):
    given, when, subject, then = split_gwt(ac['desc'])
    normative = then if then else ac['desc']
    if subject:
        normative = '%s SHALL %s' % (subject, normative)
    lines = ['### Requirement: %s' % ac['id'], '']
    lines.append(normative)
    lines.append('')
    lines.append('#### Scenario: %s' % ac['id'])
    if given:
        lines.append('- **GIVEN** %s' % given)
    if when:
        lines.append('- **WHEN** %s' % when)
    lines.append('- **THEN** %s' % (then if then else ac['desc']))
    lines.append('')
    return '\n'.join(lines)


def build_capability_spec(acs, deltas_only):
    """生成 OpenSpec 的 spec.md 文本。
    deltas_only=True 时只输出有 delta 标记的条目(用于 change 目录);
    False 时输出完整当前状态(用于 openspec/specs/ 累积)。"""
    sections = []
    if deltas_only:
        for tag, title in (('ADDED', 'ADDED Requirements'),
                           ('MODIFIED', 'MODIFIED Requirements'),
                           ('REMOVED', 'REMOVED Requirements')):
            picked = [a for a in acs if a['delta'] == tag]
            if not picked:
                continue
            body = ['## %s' % title, '']
            for ac in picked:
                body.append(ac_to_requirement(ac))
            sections.append('\n'.join(body))
    else:
        current = [a for a in acs if a['delta'] != 'REMOVED']
        if current:
            body = ['## Requirements', '']
            for ac in current:
                body.append(ac_to_requirement(ac))
            sections.append('\n'.join(body))
    return '\n'.join(sections)


def build_openspec_proposal(feature, proposal_text):
    """把 SDD 的 proposal 转成 OpenSpec 的 Why / What Changes / Impact 三段式。"""
    lines = proposal_text.splitlines()
    why, what, impact = [], [], []
    mode = None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('#'):
            # 只认这几个小节,其余小节(mode=None)的内容一律跳过
            if stripped.startswith('### 1.1'):
                mode = 'why'
            elif stripped.startswith('### 1.2'):
                mode = 'why'      # 目标也是动机的一部分
            elif stripped.startswith('### 2.1'):
                mode = 'what'
            elif stripped.startswith('### 2.2'):
                mode = 'impact'
            else:
                mode = None
            continue
        if stripped.startswith('<!--') or stripped.startswith('|'):
            continue
        if not stripped:
            continue
        if mode == 'why':
            why.append(stripped)
        elif mode == 'what':
            what.append(stripped)
        elif mode == 'impact':
            impact.append(re.sub(r'^[-*+]\s*', '', stripped))

    out = ['## Why', '']
    out.extend(why or ['<从 proposal.md §1.1 背景与 §1.2 目标提炼>'])
    out.append('')
    out.append('## What Changes')
    out.append('')
    out.extend(what or ['<从 proposal.md §2.1 做什么提炼>'])
    out.append('')
    out.append('## Impact')
    out.append('')
    out.append('- Affected specs: `%s`' % feature)
    out.append('- Affected code: 见 design.md §1.1 模块划分')
    if impact:
        out.append('')
        out.append('本期明确排除:')
        for item in impact:
            out.append('- %s' % item)
    out.append('')
    return '\n'.join(out)


def build_openspec_tasks(tasks_text):
    """把 SDD 的 tasks 转成 OpenSpec 的复选框编号格式,并保留验收标准与验证命令。"""
    out = ['## 1. 实现任务', '']
    idx = 0
    lines = tasks_text.splitlines()
    for i, line in enumerate(lines):
        m = TASK_HEADING_RE.match(line.strip())
        if not m:
            continue
        idx += 1
        done = (m.group(1) or ' ').lower() == 'x'
        title = m.group(3).strip() or m.group(2)
        out.append('- [%s] 1.%d %s' % ('x' if done else ' ', idx, title))
        # 收集该 Task 的字段,保留验收标准与验证命令
        fields = {}
        for follow in lines[i + 1:]:
            if follow.strip().startswith('#'):
                break
            fm = re.match(r'^\s*[-*]\s*\*\*(.+?)\*\*\s*[:：]\s*(.*)$', follow)
            if fm:
                fields[fm.group(1).strip()] = fm.group(2).strip()
        if fields.get('验收标准'):
            out.append('  - 验收标准: %s' % fields['验收标准'])
        if fields.get('验证命令'):
            out.append('  - 验证命令: %s' % fields['验证命令'])
        if fields.get('依赖') and fields['依赖'] not in ('无',):
            out.append('  - 依赖: %s' % fields['依赖'])
    out.append('')
    return '\n'.join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description='把 SDD 规范导出为 OpenSpec 结构')
    ap.add_argument('--feature', required=True, help='feature 名(对应 specs/<feature>/)')
    ap.add_argument('--root', default='.', help='项目根目录(默认当前目录)')
    ap.add_argument('--capability', help='OpenSpec capability 名(默认取 feature 名)')
    ap.add_argument('--dry-run', action='store_true', help='只打印将要写入的文件')
    args = ap.parse_args(argv)

    root = os.path.abspath(args.root)
    src = os.path.join(root, 'specs', args.feature)
    if not os.path.isdir(src):
        sys.stderr.write('找不到规范目录: %s\n' % src)
        return 1

    proposal_path = os.path.join(src, 'proposal.md')
    if not os.path.isfile(proposal_path):
        sys.stderr.write('找不到 proposal.md,无法导出: %s\n' % proposal_path)
        return 1

    proposal_text = read(proposal_path)
    design_text = read(os.path.join(src, 'design.md'))
    tasks_text = read(os.path.join(src, 'tasks.md'))
    acs = parse_acs(proposal_text)
    if not acs:
        sys.stderr.write('proposal.md 中没有解析到任何验收标准(AC),无法导出\n')
        return 1

    capability = args.capability or args.feature
    change_dir = os.path.join(root, 'openspec', 'changes', args.feature)
    cap_dir = os.path.join(change_dir, 'specs', capability)

    files = {
        os.path.join(change_dir, 'proposal.md'): build_openspec_proposal(args.feature, proposal_text),
        os.path.join(change_dir, 'design.md'): design_text,
        os.path.join(change_dir, 'tasks.md'): build_openspec_tasks(tasks_text),
        os.path.join(cap_dir, 'spec.md'): build_capability_spec(acs, deltas_only=True),
        os.path.join(root, 'openspec', 'specs', capability, 'spec.md'):
            build_capability_spec(acs, deltas_only=False),
    }

    delta_count = len([a for a in acs if a['delta']])
    print('源规范: %s' % src)
    print('目标: openspec/ (%d 个文件)' % len(files))
    print('验收标准 %d 条,其中带 delta 标记 %d 条' % (len(acs), delta_count))
    if delta_count == 0:
        print('提示:没有 delta 标记,change 的 spec.md 会是空的——'
              '在 AC 行尾加 <!-- ADDED --> 即可导出本轮变更')
    print('')
    for path, content in files.items():
        rel = os.path.relpath(path, root).replace('\\', '/')
        print('  %s (%d 字符)' % (rel, len(content)))
        if not args.dry_run:
            write(path, content)

    if args.dry_run:
        print('')
        print('(dry-run,未写入)')
    else:
        print('')
        print('导出完成。用 OpenSpec 时:`openspec validate --strict`')
    return 0


if __name__ == '__main__':
    sys.exit(main())

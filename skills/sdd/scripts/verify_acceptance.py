#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_acceptance.py — 验收报告行为自检

校验 `specs/<feature>/acceptance-report.md` 是否**真的**把每条验收标准都验过了,
而不是只写了一份看起来完整的报告。检查项:

  1. 报告存在,且 §1 结论 / §2 AC 回放矩阵 / §3 接口一致性 / §4 架构一致性 / §5 性能实测 齐全
  2. proposal 的每一条 AC 在回放矩阵里都有对应行
  3. 每一行都有明确结果(通过 / 未通过),不能留空
  4. 结果为「通过」的 AC,其覆盖的 Task 必须已在 tasks.md 中勾选 [x]
     —— 防止「Task 还没做完,报告却写通过」
  5. 结论与实测结果一致:有未通过项却宣布「通过」→ 错误;全部通过却宣布「不通过」→ 警告
  6. proposal 的每条性能 AC 在 §5 性能实测表里都有实测行

用法:
    python verify_acceptance.py --feature <feature> [--root .] [--json]

退出码: 0 = 通过; 1 = 存在错误; 2 = 用法/IO 错误
"""

import argparse
import json
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
NUMBER_RE = re.compile(r'^(\d+(?:\.\d+)*)\.?\s+(.*)$')
HEADING_RE = re.compile(r'^(#{1,6})\s+(.*?)\s*$')
TASK_HEADING_RE = re.compile(r'^#{2,4}\s*(?:\[([ xX])\]\s*)?(TASK-\d{3})\s*[:：]?\s*(.*)$')


class Report(object):
    def __init__(self):
        self.items = []

    def error(self, where, message):
        self.items.append({'level': 'error', 'where': where, 'message': message})

    def warn(self, where, message):
        self.items.append({'level': 'warning', 'where': where, 'message': message})

    @property
    def errors(self):
        return [i for i in self.items if i['level'] == 'error']

    @property
    def warnings(self):
        return [i for i in self.items if i['level'] == 'warning']


def read(path):
    if not os.path.isfile(path):
        return ''
    with open(path, 'r', encoding='utf-8') as fh:
        return fh.read()


def parse_headings(lines):
    headings = []
    in_fence = False
    for i, ln in enumerate(lines):
        if ln.strip().startswith('```'):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = HEADING_RE.match(ln)
        if m:
            headings.append({'level': len(m.group(1)), 'title': m.group(2).strip(), 'line': i})
    for idx, h in enumerate(headings):
        end = len(lines)
        for j in range(idx + 1, len(headings)):
            if headings[j]['level'] <= h['level']:
                end = headings[j]['line']
                break
        h['end'] = end
    return headings


def numbered(headings):
    out = {}
    for h in headings:
        m = NUMBER_RE.match(h['title'])
        if m:
            out.setdefault(m.group(1), h)
    return out


def section_lines(lines, heading):
    if heading is None:
        return []
    return lines[heading['line'] + 1:heading['end']]


def parse_tables(lines):
    tables = []
    i, n = 0, len(lines)
    while i < n:
        if lines[i].strip().startswith('|'):
            block = []
            while i < n and lines[i].strip().startswith('|'):
                block.append(lines[i].strip())
                i += 1
            if len(block) >= 2 and re.match(r'^\|[\s:\-|]+\|$', block[1]):
                header = [c.strip() for c in block[0].strip().strip('|').split('|')]
                rows = [[c.strip() for c in r.strip().strip('|').split('|')]
                        for r in block[2:]]
                tables.append({'header': header, 'rows': rows})
            continue
        i += 1
    return tables


def table_by_header(tables, *keywords):
    for t in tables:
        head = ' '.join(t['header'])
        if all(k in head for k in keywords):
            return t
    return None


def cell_result(cell):
    """把结果单元格解析成 pass / fail / unknown 。"""
    c = cell.strip()
    if '❌' in c or '未通过' in c or '不通过' in c or '失败' in c or '未达标' in c:
        return 'fail'
    if '✅' in c or '通过' in c or 'PASS' in c.upper() or '达标' in c:
        return 'pass'
    return 'unknown'


def parse_conclusion(body):
    """从 §1 结论正文里解析结论。注意 '不通过' 含 '通过',要先判否定。"""
    for line in body:
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        if '<' in stripped and '>' in stripped:
            continue  # 还是模板占位符,不算结论
        if '有条件通过' in stripped:
            return '有条件通过'
        if '不通过' in stripped:
            return '不通过'
        if '通过' in stripped:
            return '通过'
    return ''


def main(argv=None):
    ap = argparse.ArgumentParser(description='验收报告行为自检')
    ap.add_argument('--feature', required=True, help='feature 名(对应 specs/<feature>/)')
    ap.add_argument('--root', default='.', help='项目根目录(默认当前目录)')
    ap.add_argument('--json', action='store_true', help='以 JSON 输出')
    args = ap.parse_args(argv)

    root = os.path.abspath(args.root)
    base = os.path.join(root, 'specs', args.feature)
    where = 'acceptance-report.md'
    report = Report()

    proposal_text = read(os.path.join(base, 'proposal.md'))
    tasks_text = read(os.path.join(base, 'tasks.md'))
    report_path = os.path.join(base, 'acceptance-report.md')

    if not os.path.isfile(report_path):
        sys.stderr.write('找不到验收报告: %s\n' % report_path)
        return 2
    if not proposal_text:
        sys.stderr.write('找不到 proposal.md,无法核对验收范围: %s\n'
                         % os.path.join(base, 'proposal.md'))
        return 2

    report_text = read(report_path)
    lines = report_text.splitlines()
    headings = parse_headings(lines)
    secs = numbered(headings)

    # 必需章节
    for num, label in (('1', '结论'), ('2', 'AC 回放矩阵'), ('3', '接口一致性'),
                       ('4', '架构一致性'), ('5', '性能实测')):
        if num not in secs:
            report.error(where, '缺少必需章节 §%s %s' % (num, label))

    # proposal 的 AC 清单
    acs = []
    for m in AC_RE.finditer(re.sub(r'<!--.*?-->', '', proposal_text)):
        if m.group(1) not in acs:
            acs.append(m.group(1))
    perf_acs = [a for a in acs if a.startswith('AC-P-')]

    # §2 回放矩阵
    matrix = {}
    if '2' in secs:
        tables = parse_tables(section_lines(lines, secs['2']))
        t = table_by_header(tables, 'AC', '结果')
        if t is None:
            report.error(where, '§2 未找到 AC 回放矩阵(表头需含「AC」与「结果」)')
        else:
            for row in t['rows']:
                ids = [m.group(1) for m in AC_RE.finditer(' '.join(row))]
                if not ids:
                    continue
                result_cell = row[-1] if row else ''
                matrix[ids[0]] = {
                    'result': cell_result(result_cell),
                    'raw': result_cell,
                    'task_cell': ' '.join(row[1:-1]) if len(row) > 2 else '',
                }
            for ac in acs:
                if ac not in matrix:
                    report.error(where, '§2 回放矩阵缺少 %s,这条验收标准没有被验证过' % ac)
            for ac, info in matrix.items():
                if info['result'] == 'unknown':
                    report.error(where, '%s 的结果「%s」无法判定,应写「✅ 通过」或「❌ 未通过」'
                                 % (ac, info['raw']))

    # tasks 的勾选状态与 AC → Task 映射
    done_tasks = set()
    ac_to_tasks = {}
    if tasks_text:
        for line in tasks_text.splitlines():
            m = TASK_HEADING_RE.match(line.strip())
            if m and (m.group(1) or ' ').lower() == 'x':
                done_tasks.add(m.group(2))
        t_lines = tasks_text.splitlines()
        t_secs = numbered(parse_headings(t_lines))
        if '4' in t_secs:
            for t in parse_tables(section_lines(t_lines, t_secs['4'])):
                for row in t['rows']:
                    ids = [m.group(1) for m in AC_RE.finditer(' '.join(row))]
                    if not ids:
                        continue
                    task_ids = set(re.findall(r'TASK-\d{3}', ' '.join(row)))
                    ac_to_tasks.setdefault(ids[0], set()).update(task_ids)

    # 结果为「通过」但 Task 未勾选
    for ac, info in matrix.items():
        if info['result'] != 'pass':
            continue
        covered = ac_to_tasks.get(ac) or set()
        if not covered:
            continue
        not_done = sorted(t for t in covered if t not in done_tasks)
        if not_done:
            report.error(where, '%s 标为通过,但覆盖它的 %s 在 tasks.md 中还没勾选完成'
                         % (ac, ', '.join(not_done)))

    # 结论与实测一致
    conclusion = ''
    if '1' in secs:
        conclusion = parse_conclusion(section_lines(lines, secs['1']))
        if not conclusion:
            report.error(where, '§1 结论没有明确写出「通过」「有条件通过」或「不通过」')
    failed = sorted(ac for ac, i in matrix.items() if i['result'] == 'fail')
    unknown = sorted(ac for ac, i in matrix.items() if i['result'] == 'unknown')
    if conclusion == '通过' and failed:
        report.error(where, '结论是「通过」,但有 %d 条验收标准未通过: %s'
                     % (len(failed), ', '.join(failed)))
    if conclusion == '通过' and unknown:
        report.error(where, '结论是「通过」,但有 %d 条验收标准结果无法判定: %s'
                     % (len(unknown), ', '.join(unknown)))
    if conclusion in ('不通过', '有条件通过') and matrix and not failed and not unknown:
        report.warn(where, '结论是「%s」,但所有验收标准都通过了——确认结论是否过严' % conclusion)

    # §5 性能实测覆盖
    if '5' in secs and perf_acs:
        tables = parse_tables(section_lines(lines, secs['5']))
        t = table_by_header(tables, '指标', '实测') or table_by_header(tables, '实测')
        covered = set()
        if t:
            for row in t['rows']:
                covered.update(m.group(1) for m in AC_RE.finditer(' '.join(row)))
        else:
            report.error(where, '§5 未找到性能实测表(表头需含「实测」)')
        for ac in perf_acs:
            if ac not in covered:
                report.error(where, '§5 性能实测缺少 %s,这条性能指标没有被实测' % ac)

    errors = report.errors
    warnings = report.warnings
    if args.json:
        print(json.dumps({
            'feature': args.feature,
            'conclusion': conclusion,
            'acs': len(acs),
            'failed': failed,
            'errors': errors,
            'warnings': warnings,
            'passed': not errors,
        }, ensure_ascii=False, indent=2))
    else:
        for item in errors:
            print('[错误] %s: %s' % (item['where'], item['message']))
        for item in warnings:
            print('[警告] %s: %s' % (item['where'], item['message']))
        print('')
        print('验收自检完成:%d 个错误,%d 个警告' % (len(errors), len(warnings)))
        if not errors:
            print('结果:通过')
        else:
            print('结果:未通过')

    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())

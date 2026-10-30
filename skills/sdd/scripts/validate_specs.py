#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
validate_specs.py — SDD 规范文档校验器

校验 specs/<feature>/ 下的 proposal.md / design.md / tasks.md 是否满足 SDD 的硬性要求:
  proposal : 章节齐全、§2.2 不做什么 >= 3 条、AC 可二值化、性能 AC 已量化、术语一致
  design   : 模块职责表含「不做什么」列、ADR 六要素齐全且编号连续、追溯矩阵覆盖全部 AC
  tasks    : Task 编号连续、标题动词开头、每个 Task 有验证命令、依赖 DAG 无环、AC 全覆盖

用法:
    python validate_specs.py --feature <feature> [--root .] [--json] [--quiet]
    python validate_specs.py --root .            # 校验 specs/ 下所有 feature

退出码: 0 = 无错误; 1 = 存在错误; 2 = 用法/IO 错误
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

# ---------------------------------------------------------------- 解析基础设施

HEADING_RE = re.compile(r'^(#{1,6})\s+(.*?)\s*$')
NUMBER_RE = re.compile(r'^(\d+(?:\.\d+)*)\.?\s+(.*)$')
BULLET_RE = re.compile(r'^\s*[-*+]\s+(.*)$')
AC_RE = re.compile(r'(?<![A-Za-z0-9])(AC-([FPE])-(\d{3}))(?![0-9])')
# delta 标记:写在条目行尾,使用 OpenSpec 同名关键字
DELTA_RE = re.compile(r'<!--\s*(ADDED|MODIFIED|REMOVED)\s*-->')
ADR_RE = re.compile(r'^ADR-(\d{3})\s*[:：]\s*(.*)$')
TASK_RE = re.compile(r'^(?:\[(?:[ xX])\]\s*)?TASK-(\d{3})\s*[:：]?\s*(.*)$')
# 占位符形如 <...>;HTML 注释(<!-- MODIFIED --> 等 delta 标记)不算占位符
PLACEHOLDER_RE = re.compile(r'<(?!!--)[^<>\n]{1,120}>')
# 形如 <...> 的占位符;排除少量 HTML 标签
HTML_TAG_RE = re.compile(
    r'^</?(br|hr|img|a|p|div|span|code|pre|b|i|em|strong|td|th|tr|table|ul|ol|li|'
    r'input|link|meta|!--|font|center|small|sup|sub)[^<>]*>$', re.I)

OBSERVABLE_HINTS = (
    '返回', '响应', '输出', '显示', '提示', '状态码', '日志', '耗时', '延迟', '拒绝',
    '成功', '失败', '保持', '等于', '不超过', '不小于', '不少于', '小于', '大于',
    '创建', '删除', '更新', '发送', '存储', '记录', '包含', '为空', '非空', '报错',
    '中断', '恢复', '一致', '不变', '阻塞', '超时', '重试', '降级', '状态', '不发生',
    'http', '错误码', '告警', '无残留', '不超', '丢失', '出现', '触发', '生效',
    '拒绝', '拒绝', '跳转', '渲染', '展示', '保留', '清除', '重置', '递增', '递减',
)

# AC 里表示「期望结果」的标记,取最后一个标记之后的部分做可观测性判断
RESULT_MARKERS = ('SHALL', 'MUST', 'SHOULD', '应当', '应该', '必须')

# 量化阈值:性能类 AC 必须有比较关系或负向保证,否则「足够快」这类不可测
THRESHOLD_RE = re.compile(
    r'(<|>|≤|≥|不超过|不少于|小于|大于|低于|高于|以内|以上|以下|至少|最多|达到)', re.I)
GUARANTEE_RE = re.compile(
    r'(不丢失|不发生|不低于|不高于|不超过|不少于|不小于|不大于|至少|不早于|不晚于|'
    r'保持|无丢失|零丢失|一致|相同|不重复|不中断)')

TASK_VERBS = (
    '实现', '编写', '添加', '接入', '配置', '部署', '修复', '重构', '优化', '补充',
    '搭建', '设计', '抽取', '封装', '集成', '迁移', '支持', '增加', '删除', '调整',
    '定义', '建立', '完成', '引入', '替换', '打包', '验证', '测试', '覆盖', '保障',
    '落地', '加载', '初始化', '生成', '改造', '拆分', '合并', '登记', '限制', '校验',
    '对接', '打通', '沉淀', '规范', '统一', '收敛', '兜底', '容错', '埋点', '监控',
    '告警', '灰度', '发布', '回滚', '升级', '适配', '兼容', '暴露', '上报', '上报',
    '查询', '检索', '过滤', '排序', '分页', '导入', '导出', '同步', '异步', '编排',
    '串联', '解耦', '隔离', '限流', '鉴权', '授权', '加密', '解密', '脱敏', '审计',
)
TASK_VERBS_EN = (
    'implement', 'add', 'create', 'build', 'write', 'support', 'fix', 'refactor',
    'configure', 'integrate', 'expose', 'migrate', 'introduce', 'extract', 'enable',
)


class Report(object):
    """收集错误与警告。"""

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


def read_text(path):
    with open(path, 'r', encoding='utf-8') as fh:
        return fh.read()


def parse_headings(lines):
    """解析标题树,返回 [{level,title,line,end}]。end = 下一个同级或更高级标题的行号。"""
    headings = []
    in_fence = False
    for i, ln in enumerate(lines):
        stripped = ln.strip()
        if stripped.startswith('```'):
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
    """把 '1.' / '2.1' 形式的标题映射为编号 -> heading。"""
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


def strip_placeholders(text):
    """移除 <...> 占位符,避免模板里的示例 AC/TASK 被当成真实条目。"""
    return PLACEHOLDER_RE.sub(' ', text)


def find_placeholders(text):
    """返回 (所在行号, 占位符内容) 列表,忽略 HTML 注释与合法 HTML 标签。"""
    found = []
    in_fence = False
    for i, ln in enumerate(text.splitlines()):
        if ln.strip().startswith('```'):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        no_comment = re.sub(r'<!--.*?-->', '', ln)
        for m in PLACEHOLDER_RE.finditer(no_comment):
            token = m.group(0)
            if HTML_TAG_RE.match(token):
                continue
            found.append((i + 1, token))
    return found


def parse_bullet_tree(lines):
    """把列表项解析成树,支持嵌套(如 ADR 的「选项」下挂多个候选)。
    返回 [{text, indent, children:[...]}] 。HTML 注释行被忽略。"""
    raw = []
    for ln in lines:
        no_comment = re.sub(r'<!--.*?-->', '', ln)
        if not no_comment.strip():
            continue
        m = BULLET_RE.match(no_comment)
        if m:
            raw.append({'indent': len(no_comment) - len(no_comment.lstrip()),
                        'text': m.group(1).strip()})
        elif raw:
            raw[-1]['text'] += ' ' + no_comment.strip()

    tree = []
    stack = []
    for it in raw:
        node = {'text': it['text'], 'indent': it['indent'], 'children': []}
        while stack and stack[-1][0] >= node['indent']:
            stack.pop()
        if stack:
            stack[-1][1]['children'].append(node)
        else:
            tree.append(node)
        stack.append((node['indent'], node))
    return tree


def flatten_tree(tree):
    """深度优先展开列表树。"""
    out = []
    for node in tree:
        out.append(node)
        out.extend(flatten_tree(node['children']))
    return out


def collect_bullets(lines):
    """展开成扁平的列表项文本列表(兼容性封装)。"""
    return [n['text'] for n in flatten_tree(parse_bullet_tree(lines))]


def parse_tables(lines):
    """解析 markdown 表格。返回 [{header:[...], rows:[[...],...]}] 。"""
    tables = []
    i = 0
    n = len(lines)
    while i < n:
        if lines[i].strip().startswith('|'):
            block = []
            while i < n and lines[i].strip().startswith('|'):
                block.append(lines[i].strip())
                i += 1
            if len(block) >= 2 and re.match(r'^\|[\s:\-|]+\|$', block[1]):
                header = split_row(block[0])
                rows = [split_row(r) for r in block[2:]]
                tables.append({'header': header, 'rows': rows})
            continue
        i += 1
    return tables


def split_row(row):
    return [c.strip() for c in row.strip().strip('|').split('|')]


def table_by_header(tables, *keywords):
    """找出表头包含全部 keywords 的表格。"""
    for t in tables:
        head = ' '.join(t['header'])
        if all(k in head for k in keywords):
            return t
    return None


def cell_text(row):
    return ' '.join(c for c in row if c).strip()


def field_value(tree, name):
    """取 '- **名称**: 值' 字段的值。值为空时回落到其子项文本(处理 ADR 的「选项」)。"""
    for node in flatten_tree(tree):
        m = re.match(r'^\*{0,2}([^*:：]+)\*{0,2}\s*[:：]\s*(.*)$', node['text'])
        if m and m.group(1).strip() == name:
            value = m.group(2).strip()
            if value:
                return value
            kids = ' '.join(k['text'] for k in node['children']).strip()
            return kids
    return None


def expected_result(desc):
    """取 AC 的期望结果片段:最后一个结果标记(SHALL/MUST/…)之后的部分。"""
    idx = -1
    for marker in RESULT_MARKERS:
        pos = desc.rfind(marker)
        if pos > idx:
            idx = pos
    if idx < 0:
        return desc
    return desc[idx:].strip()


def looks_observable(text):
    """判断一段文字是否是可观测、可二值判定的结果。"""
    if re.search(r'\d', text):
        return True
    low = text.lower()
    return any(h in low for h in OBSERVABLE_HINTS)


def find_cycle(graph):
    """在 node -> deps 图中找环。返回环路径列表,无环返回 None。"""
    color = dict((n, 0) for n in graph)
    stack = []

    def dfs(node):
        color[node] = 1
        stack.append(node)
        for dep in sorted(graph.get(node, ())):
            state = color.get(dep, 0)
            if state == 1:
                idx = stack.index(dep)
                return stack[idx:] + [dep]
            if state == 0:
                found = dfs(dep)
                if found:
                    return found
        stack.pop()
        color[node] = 2
        return None

    for node in sorted(graph):
        if color.get(node, 0) == 0:
            found = dfs(node)
            if found:
                return found
    return None


# ---------------------------------------------------------------- proposal 校验

def validate_proposal(path, report):
    where = os.path.basename(path)
    if not os.path.isfile(path):
        report.error(where, 'proposal.md 不存在')
        return {'acs': [], 'terms': [], 'asms': []}

    text = read_text(path)
    lines = text.splitlines()
    headings = parse_headings(lines)
    secs = numbered(headings)

    for num, label in (('1', '背景与目标'), ('2', '功能范围'), ('3', '验收标准')):
        if num not in secs:
            report.error(where, '缺少必需章节 §%s %s' % (num, label))
    for num, label in (('4', '术语定义'), ('5', '开放问题与假设'), ('6', '变更记录')):
        if num not in secs:
            report.warn(where, '建议补充 §%s %s' % (num, label))

    for num in ('1.1', '1.2', '1.3', '2.1', '2.2', '3.1', '3.2', '3.3'):
        if num not in secs:
            report.error(where, '缺少必需小节 §%s' % num)

    # §2.2 不做什么 >= 3(只数顶层条目)
    tree22 = parse_bullet_tree(section_lines(lines, secs.get('2.2')))
    n22 = len([n for n in tree22 if n['text'].strip()])
    if '2.2' in secs and n22 < 3:
        report.error(where, '§2.2「不做什么」至少 3 条,当前 %d 条' % n22)

    # 收集 AC
    clean = strip_placeholders(text)
    ac_hits = []
    for m in AC_RE.finditer(clean):
        ac_hits.append((m.group(1), m.group(2), m.group(3)))
    seen = {}
    for ac_id, kind, num in ac_hits:
        if ac_id in seen:
            report.error(where, '验收标准编号重复: %s' % ac_id)
        seen[ac_id] = True

    # delta 标记
    deltas = {}
    for ln in clean.splitlines():
        m = AC_RE.search(ln)
        if not m:
            continue
        d = DELTA_RE.search(ln)
        if d:
            deltas[m.group(1)] = d.group(1)

    # 按小节归属
    by_section = {}
    for sub, kind in (('3.1', 'F'), ('3.2', 'P'), ('3.3', 'E')):
        sec_text = strip_placeholders(' '.join(section_lines(lines, secs.get(sub))))
        ids = [m.group(1) for m in AC_RE.finditer(sec_text)]
        by_section[sub] = ids
        for ac_id in ids:
            m = AC_RE.search(ac_id)
            actual = re.match(r'AC-([FPE])-', ac_id).group(1)
            if actual != kind:
                report.error(where, '%s 应出现在 §%s（编号前缀 %s 与小节不符）' % (ac_id, sub, actual))

    # 性能 AC 必须量化到阈值
    for ac_id in by_section.get('3.2', []):
        line = find_ac_line(clean, ac_id)
        if not line:
            continue
        if not re.search(r'\d', line):
            report.error(where, '性能验收标准 %s 必须量化为数字' % ac_id)
        elif not (THRESHOLD_RE.search(line) or GUARANTEE_RE.search(line)):
            report.error(where, '性能验收标准 %s 未给出量化阈值:要写成「< 200ms」「≥ 500 QPS」'
                                '这类可判定的形式,而不是「足够快」' % ac_id)

    # 每条 AC 必须有可观测结果(只看期望结果片段,避免前半句的数字干扰)
    for ac_id in sorted(seen):
        line = find_ac_line(clean, ac_id)
        if line is None:
            continue
        desc = re.sub(r'^\**AC-[FPE]-\d{3}\**\s*[:：]?\s*', '', line).strip()
        if not desc:
            report.error(where, '验收标准 %s 缺少描述' % ac_id)
            continue
        result = expected_result(desc)
        if not looks_observable(result):
            report.error(where, '验收标准 %s 的期望结果不可观测,无法二值判定: %s'
                         % (ac_id, result[:60]))

    # 编号连续性
    for kind in ('F', 'P', 'E'):
        nums = sorted(int(re.match(r'AC-%s-(\d{3})' % kind, a).group(1))
                      for a in seen if a.startswith('AC-%s-' % kind))
        for idx, n in enumerate(nums, start=1):
            if n != idx:
                report.warn(where, 'AC-%s 编号不连续(期望 %03d,实际 %03d),编号是追溯锚点,不要重排'
                            % (kind, idx, n))
                break

    # 术语
    terms = []
    if '4' in secs:
        tables = parse_tables(section_lines(lines, secs['4']))
        t = tables[0] if tables else None
        if t:
            for row in t['rows']:
                if row and row[0]:
                    terms.append(row[0])
        body = strip_placeholders(' '.join(section_lines(lines, secs.get('1'))) +
                                  ' '.join(section_lines(lines, secs.get('2'))) +
                                  ' '.join(section_lines(lines, secs.get('3'))))
        for term in terms:
            if term and term not in body:
                report.warn(where, '术语「%s」在正文 §1-§3 中未出现,要么用起来要么删掉' % term)

    # 假设
    asms = []
    if '5' in secs:
        for b in collect_bullets(section_lines(lines, secs['5'])):
            m = re.search(r'(ASM-\d{3})', b)
            if m:
                asms.append(m.group(1))

    # 细粒度
    if len(lines) > 300:
        report.warn(where, 'proposal 共 %d 行,超过 300 行建议拆成多个 feature' % len(lines))
    if len(seen) > 30:
        report.warn(where, '验收标准 %d 条,超过 30 条建议按子域拆分' % len(seen))

    for line_no, token in find_placeholders(text):
        report.error(where, '第 %d 行仍有未填写的占位符 %s' % (line_no, token))

    return {'acs': sorted(seen), 'terms': terms, 'asms': asms, 'deltas': deltas}


def find_ac_line(clean_text, ac_id):
    for ln in clean_text.splitlines():
        if ac_id in ln:
            return ln.strip()
    return None


# ---------------------------------------------------------------- design 校验

def validate_design(path, report, proposal):
    where = os.path.basename(path)
    if not os.path.isfile(path):
        report.error(where, 'design.md 不存在')
        return {'adrs': []}

    text = read_text(path)
    lines = text.splitlines()
    headings = parse_headings(lines)
    secs = numbered(headings)

    for num, label in (('1', '系统架构'), ('2', '技术选型 ADR'),
                       ('3', '接口契约'), ('4', '非功能性约束')):
        if num not in secs:
            report.error(where, '缺少必需章节 §%s %s' % (num, label))
    for num, label in (('5', '依赖与假设'), ('6', '追溯矩阵'), ('7', '变更记录')):
        if num not in secs:
            report.warn(where, '建议补充 §%s %s' % (num, label))

    for num in ('1.1', '3.1', '3.2'):
        if num not in secs:
            report.error(where, '缺少必需小节 §%s' % num)
    for num in ('4.1', '4.2', '4.3', '4.4'):
        if num not in secs:
            report.warn(where, '建议补充小节 §%s（性能/安全/错误处理/可运维性）' % num)

    # §1.1 模块表必须含「不做什么」列
    if '1.1' in secs:
        tables = parse_tables(section_lines(lines, secs['1.1']))
        mod_table = table_by_header(tables, '职责')
        if mod_table is None:
            report.error(where, '§1.1 未找到模块划分表(表头需含「职责」)')
        else:
            head_text = ' '.join(mod_table['header'])
            if '不做什么' not in head_text:
                report.error(where, '§1.1 模块表缺少「不做什么」列——职责边界是设计评审的核心')
            else:
                col = [i for i, h in enumerate(mod_table['header']) if '不做什么' in h][0]
                for row in mod_table['rows']:
                    name = row[0] if row else ''
                    val = row[col] if col < len(row) else ''
                    if not val or val in ('无', '-', 'N/A', 'n/a'):
                        report.error(where, '模块「%s」没有写明不做什么' % name)

    # ADR
    adr_headings = []
    for h in headings:
        m = ADR_RE.match(h['title'])
        if m:
            adr_headings.append((int(m.group(1)), h))
    if not adr_headings:
        report.warn(where, '§2 没有发现任何 ADR(格式: ### ADR-001: 标题)')
    adr_numbers = sorted(n for n, _ in adr_headings)
    for idx, n in enumerate(adr_numbers, start=1):
        if n != idx:
            report.error(where, 'ADR 编号不连续:期望 ADR-%03d,实际 ADR-%03d' % (idx, n))
            break
    valid_status = ('提议', '已采纳', '已废弃')
    for n, h in adr_headings:
        body = section_lines(lines, h)
        tree = parse_bullet_tree(body)
        for field in ('状态', '背景', '选项', '决策', '理由'):
            val = field_value(tree, field)
            if val is None or not val.strip():
                report.error(where, 'ADR-%03d 缺少「%s」' % (n, field))
        status = field_value(tree, '状态')
        if status:
            ok = any(status.startswith(s) or (s == '已被 ADR-xxx 取代' and '已被' in status)
                     for s in valid_status) or '已被 ADR-' in status
            if not ok:
                report.error(where, 'ADR-%03d 状态「%s」不合法(应为 提议/已采纳/已废弃/已被 ADR-xxx 取代)'
                             % (n, status))

    # 追溯矩阵:覆盖 proposal 全部 AC
    trace_map = {}
    if '6' in secs and proposal.get('acs'):
        tables = parse_tables(section_lines(lines, secs['6']))
        trace = table_by_header(tables, 'AC')
        covered = set()
        if trace:
            for row in trace['rows']:
                cell_text_ = strip_placeholders(cell_text(row))
                ids = [m.group(1) for m in AC_RE.finditer(cell_text_)]
                for ac_id in ids:
                    others = [c for c in row if c and ac_id not in c]
                    trace_map.setdefault(ac_id, []).extend(others)
                covered.update(ids)
        else:
            report.error(where, '§6 追溯矩阵未找到(表头需含「AC」)')
        for ac in proposal['acs']:
            if ac not in covered:
                tag = (proposal.get('deltas') or {}).get(ac)
                extra = '(本轮标记为 %s,新增需求尚未落到设计)' % tag if tag == 'ADDED' else ''
                report.error(where, '§6 追溯矩阵未覆盖 %s%s' % (ac, extra))

    # §5 假设表
    if '5' in secs and proposal.get('asms'):
        tables = parse_tables(section_lines(lines, secs['5']))
        t = tables[0] if tables else None
        got = set()
        if t:
            for row in t['rows']:
                cell_text_ = cell_text(row)
                got.update(re.findall(r'ASM-\d{3}', cell_text_))
        for asm in proposal['asms']:
            if asm not in got:
                report.warn(where, '§5 未承接 proposal 的假设 %s' % asm)

    for line_no, token in find_placeholders(text):
        report.error(where, '第 %d 行仍有未填写的占位符 %s' % (line_no, token))

    return {'adrs': adr_numbers, 'trace': trace_map}


# ---------------------------------------------------------------- tasks 校验

def validate_tasks(path, report, proposal):
    where = os.path.basename(path)
    if not os.path.isfile(path):
        report.error(where, 'tasks.md 不存在')
        return {'tasks': [], 'done': 0}

    text = read_text(path)
    lines = text.splitlines()
    headings = parse_headings(lines)
    secs = numbered(headings)

    if '1' not in secs:
        report.error(where, '缺少必需章节 §1 任务列表')
    for num, label in (('2', '依赖关系图'), ('3', '推荐执行顺序'), ('4', '追溯矩阵')):
        if num not in secs:
            report.warn(where, '建议补充 §%s %s' % (num, label))

    # Task 段落
    tasks = []
    for h in headings:
        m = TASK_RE.match(h['title'])
        if m:
            tasks.append({'num': int(m.group(1)), 'title': m.group(2).strip(), 'heading': h})
    if not tasks:
        report.error(where, '§1 没有发现任何 Task(格式: ### TASK-001: 动词开头的标题)')

    numbers = sorted(t['num'] for t in tasks)
    for idx, n in enumerate(numbers, start=1):
        if n != idx:
            report.error(where, 'Task 编号不连续:期望 TASK-%03d,实际 TASK-%03d' % (idx, n))
            break

    graph = {}
    task_ids = set(numbers)
    for t in tasks:
        tid = 'TASK-%03d' % t['num']
        body = section_lines(lines, t['heading'])
        tree = parse_bullet_tree(body)

        # 标题动词开头
        title = strip_placeholders(t['title'])
        if title:
            head = title[0]
            ok = any(title.startswith(v) for v in TASK_VERBS) or \
                 any(title.lower().startswith(v) for v in TASK_VERBS_EN) or \
                 head in '实现编写添加接入配置部署修复重构优化补充搭建设计抽取封装集成迁移支持增加删除调整定义建立完成引入替换打包验证测试覆盖保障落地'
            if not ok:
                report.error(where, '%s 标题「%s」未以动词开头,应改成「实现…」「接入…'
                             '「配置…」这类动宾结构' % (tid, title[:30]))

        for field in ('描述', '输入', '输出', '验收标准', '依赖', '验证命令'):
            val = field_value(tree,field)
            if val is None or not val.strip():
                report.error(where, '%s 缺少「%s」' % (tid, field))

        cmd = field_value(tree,'验证命令')
        if cmd:
            cmd = cmd.strip().strip('`')
            if not cmd or len(cmd) < 3:
                report.error(where, '%s 的验证命令无效' % tid)
            elif not re.search(r'\s', cmd):
                report.warn(where, '%s 的验证命令「%s」看起来不像可执行命令' % (tid, cmd))

        deps = set()
        dep_field = field_value(tree,'依赖') or ''
        for m in re.finditer(r'TASK-(\d{3})', dep_field):
            deps.add(int(m.group(1)))
        for d in sorted(deps):
            if d not in task_ids:
                report.error(where, '%s 依赖了不存在的 TASK-%03d' % (tid, d))
            if d == t['num']:
                report.error(where, '%s 依赖了自己' % tid)
        graph[t['num']] = deps

    # DAG 无环
    cycle = find_cycle(graph)
    if cycle:
        report.error(where, '任务依赖存在环: %s' % ' → '.join('TASK-%03d' % c for c in cycle))

    # 追溯矩阵:覆盖 proposal 全部 AC
    trace_map = {}
    if '4' in secs and proposal.get('acs'):
        tables = parse_tables(section_lines(lines, secs['4']))
        trace = table_by_header(tables, 'AC')
        covered = set()
        if trace:
            for row in trace['rows']:
                cell_text_ = strip_placeholders(cell_text(row))
                ids = [m.group(1) for m in AC_RE.finditer(cell_text_)]
                for ac_id in ids:
                    others = [c for c in row if c and ac_id not in c]
                    trace_map.setdefault(ac_id, []).extend(others)
                covered.update(ids)
        else:
            report.error(where, '§4 追溯矩阵未找到(表头需含「AC」)')
        for ac in proposal['acs']:
            if ac not in covered:
                tag = (proposal.get('deltas') or {}).get(ac)
                extra = '(本轮标记为 %s,新增需求尚未拆成 Task)' % tag if tag == 'ADDED' else ''
                report.error(where, '§4 追溯矩阵未覆盖 %s,存在无人实现的需求%s' % (ac, extra))

    # 任务总数元信息
    meta = parse_tables(lines[:12])
    meta_table = table_by_header(meta, '项目') if meta else None
    if meta_table:
        for row in meta_table['rows']:
            if len(row) >= 2 and row[0].strip() == '任务总数':
                declared = row[1].strip()
                if declared.isdigit() and int(declared) != len(numbers):
                    report.warn(where, '§0 声明任务总数 %s,实际 %d 个' % (declared, len(numbers)))

    # 勾选进度(标题式:### [x] TASK-001: ...)
    done = len(re.findall(r'^#{2,4}\s*\[[xX]\]\s*TASK-\d{3}', text, re.M))

    for line_no, token in find_placeholders(text):
        report.error(where, '第 %d 行仍有未填写的占位符 %s' % (line_no, token))

    return {'tasks': numbers, 'done': done, 'trace': trace_map}


# ---------------------------------------------------------------- 入口

def analyze_delta_impact(report, feature, proposal, design, tasks):
    """delta 变更的下游影响:标记了 MODIFIED/REMOVED 的 AC 需要复核设计与任务。"""
    deltas = proposal.get('deltas') or {}
    if not deltas:
        return
    where = 'specs/%s' % feature
    d_trace = design.get('trace') or {}
    t_trace = tasks.get('trace') or {}
    for ac_id in sorted(deltas):
        tag = deltas[ac_id]
        if tag == 'ADDED':
            continue  # 追溯覆盖检查已报错,ADDED 的缺口就是缺覆盖
        targets = []
        if ac_id in d_trace:
            targets.append('design ' + ', '.join(d_trace[ac_id]))
        if ac_id in t_trace:
            targets.append('tasks ' + ', '.join(t_trace[ac_id]))
        if targets:
            report.warn(where, '%s 标记为 %s,下游需复核 → %s'
                        % (ac_id, tag, '; '.join(targets)))
        else:
            report.warn(where, '%s 标记为 %s,但追溯矩阵里已找不到下游条目,确认是否同步清理'
                        % (ac_id, tag))


def validate_feature(root, feature, report):
    base = os.path.join(root, 'specs', feature)
    proposal = validate_proposal(os.path.join(base, 'proposal.md'), report)
    design = validate_design(os.path.join(base, 'design.md'), report, proposal)
    tasks = validate_tasks(os.path.join(base, 'tasks.md'), report, proposal)
    analyze_delta_impact(report, feature, proposal, design, tasks)


def main(argv=None):
    ap = argparse.ArgumentParser(description='校验 SDD 规范文档')
    ap.add_argument('--feature', help='要校验的 feature 名(对应 specs/<feature>/)')
    ap.add_argument('--root', default='.', help='项目根目录(默认当前目录)')
    ap.add_argument('--json', action='store_true', help='以 JSON 输出')
    ap.add_argument('--quiet', action='store_true', help='只输出错误')
    args = ap.parse_args(argv)

    root = os.path.abspath(args.root)
    specs_dir = os.path.join(root, 'specs')
    if not os.path.isdir(specs_dir):
        sys.stderr.write('找不到目录: %s\n' % specs_dir)
        return 2

    if args.feature:
        features = [args.feature]
    else:
        features = sorted(d for d in os.listdir(specs_dir)
                          if os.path.isdir(os.path.join(specs_dir, d)))
    if not features:
        sys.stderr.write('%s 下没有任何 feature 目录\n' % specs_dir)
        return 2

    report = Report()
    for feat in features:
        validate_feature(root, feat, report)

    errors = report.errors
    warnings = report.warnings

    if args.json:
        print(json.dumps({
            'root': root,
            'features': features,
            'errors': errors,
            'warnings': warnings,
            'passed': not errors,
        }, ensure_ascii=False, indent=2))
    else:
        for feat in features:
            print('=== specs/%s ===' % feat)
        print('')
        for item in errors:
            print('[错误] %s: %s' % (item['where'], item['message']))
        for item in warnings:
            if args.quiet:
                continue
            print('[警告] %s: %s' % (item['where'], item['message']))
        print('')
        print('校验完成:%d 个错误,%d 个警告' % (len(errors), len(warnings)))
        if not errors:
            print('结果:通过')
        else:
            print('结果:未通过')

    return 1 if errors else 0


if __name__ == '__main__':
    sys.setrecursionlimit(10000)
    sys.exit(main())

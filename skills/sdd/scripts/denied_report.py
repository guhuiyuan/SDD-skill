#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
denied_report.py — 查「最近这个会话里,哪些命令被拒了、为什么」

命令被拒时,终端上只看得见一行报错,不知道是:
- 落到了服务端分类器(分类器不可达 → auto 模式整体失效)
- 还是被 hook 判成不放行(白名单没这条 / 危险 / 路径越界)
- 还是命令本身跑失败了(退出码非 0,跟权限无关)

这个脚本直接读会话 transcript,把**被拒的工具调用**和原因列出来。

用法:

    python denied_report.py                  # 当前目录对应的项目,最近一个会话
    python denied_report.py --root <项目根>   # 指定项目
    python denied_report.py --limit 40       # 多列几条
    python denied_report.py --all-sessions   # 把该项目所有会话都扫一遍

输出:每条一行「命令 → 原因」,末尾给一个分类统计。
"""

import argparse
import json
import os
import sys

for _stream in ('stdout', 'stderr'):
    try:
        getattr(sys, _stream).reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

CLAIM = 'project'
TRANSCRIPTS = os.path.join(os.path.expanduser('~'), '.claude', 'projects')


def project_key(root):
    """把项目路径变成 transcript 目录名(与 harness 的规则一致)。"""
    path = os.path.abspath(root).replace('\\', '/')
    out = []
    for ch in path:
        if ch.isalnum() or ch in '-_.':
            out.append(ch)
        else:
            out.append('-')
    return ''.join(out)


def sessions(root):
    """产出该项目的会话 transcript 路径(新的在前)。"""
    directory = os.path.join(TRANSCRIPTS, project_key(root))
    if not os.path.isdir(directory):
        return []
    files = []
    for name in os.listdir(directory):
        # 只取顶层会话文件:uuid(8-4-4-4-12)+ .jsonl
        if not name.endswith('.jsonl') or name.count('-') != 4:
            continue
        path = os.path.join(directory, name)
        if os.path.isfile(path):
            files.append(path)
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return files


def collect(path):
    """扫一个 transcript,返回 [(工具名, 命令或入参摘要, 错误文本)]。"""
    uses, results = {}, []
    last_use = ('', {})
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as fh:
            for line in fh:
                line = line.strip()
                if not line or ('"tool_use"' not in line
                                and '"tool_result"' not in line):
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                message = obj.get('message')
                if not isinstance(message, dict):
                    continue
                for block in (message.get('content') or []):
                    if not isinstance(block, dict):
                        continue
                    if block.get('type') == 'tool_use':
                        entry = (block.get('name') or '', block.get('input') or {})
                        uses[block.get('id')] = entry
                        last_use = entry
                    elif block.get('type') == 'tool_result':
                        results.append((block.get('tool_use_id') or '',
                                        block.get('content'),
                                        bool(block.get('is_error'))))
    except Exception as exc:
        print('读取失败 %s: %r' % (path, exc), file=sys.stderr)
        return []
    out = []
    for tid, content, is_error in results:
        if not is_error:
            continue
        name, inp = uses.get(tid) or last_use
        text = content if isinstance(content, str) else json.dumps(
            content, ensure_ascii=False)
        if name == 'Bash':
            summary = (inp or {}).get('command') or ''
        elif name:
            summary = '%s(%s)' % (name, json.dumps(inp, ensure_ascii=False)[:120])
        else:
            summary = '(未知工具)'
        out.append((name, summary, text))
    return out


def classify(text):
    """把错误文本归类。"""
    if 'Auto mode is unavailable' in text or 'safety check does not run' in text:
        return '分类器不可达(auto 模式整体失效)'
    if 'user doesn\'t want to proceed' in text or 'rejected' in text:
        return '用户拒绝'
    if 'String to replace not found' in text:
        return '编辑失败(与权限无关)'
    if 'not found' in text.lower() or 'No such file' in text:
        return '文件/命令不存在'
    if 'Exit code' in text:
        return '命令跑失败(与权限无关)'
    return '其它'


def main(argv=None):
    ap = argparse.ArgumentParser(description='查最近会话里被拒的命令与原因')
    ap.add_argument('--root', default=os.getcwd(), help='项目根(默认当前目录)')
    ap.add_argument('--limit', type=int, default=20, help='最多列几条')
    ap.add_argument('--all-sessions', action='store_true',
                    help='扫该项目所有会话')
    args = ap.parse_args(argv)

    files = sessions(args.root)
    if not files:
        print('找不到 %s 的会话 transcript(%s 下没有)'
              % (args.root, TRANSCRIPTS))
        return 1
    targets = files if args.all_sessions else files[:1]
    print('项目: %s' % os.path.abspath(args.root))
    print('会话: %s' % ', '.join(os.path.basename(p)[:8] for p in targets))
    print('')

    total = 0
    counts = {}
    for path in targets:
        rows = collect(path)
        if not rows:
            continue
        for name, summary, text in rows[-args.limit:]:
            total += 1
            kind = classify(text)
            counts[kind] = counts.get(kind, 0) + 1
            one = ' '.join(summary.split())
            if len(one) > 150:
                one = one[:150] + '…'
            err = ' '.join(text.split())
            if len(err) > 160:
                err = err[:160] + '…'
            print('[%s] %s' % (kind, one))
            print('        ↳ %s' % err)
    print('')
    print('共 %d 条被拒的工具调用。分类统计:' % total)
    for kind, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print('  %-28s %d' % (kind, n))
    if not total:
        print('  (没有被拒的工具调用)')
    return 0


if __name__ == '__main__':
    sys.exit(main())

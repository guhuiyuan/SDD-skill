#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
init_specs.py — 创建 SDD 规范目录骨架

在 <root>/specs/<feature>/ 下生成 proposal.md / design.md / tasks.md 三份骨架,
并预填项目名、feature 名与日期。已存在的文件不会被覆盖(除非 --force)。

用法:
    python init_specs.py <feature> [--root .] [--force] [--list]

退出码: 0 = 成功; 1 = 用法错误
"""

import argparse
import datetime
import os
import re
import shutil
import sys

# Windows 控制台默认可能是 GBK,强制 UTF-8 输出避免中文乱码或编码异常
for _stream in ('stdout', 'stderr'):
    try:
        getattr(sys, _stream).reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            os.pardir, 'assets', 'templates')
TEMPLATE_DIR = os.path.normpath(TEMPLATE_DIR)

DOCS = ('proposal.md', 'design.md', 'tasks.md')


def display_name(feature):
    """user-auth → User Auth,用于文档标题。"""
    words = re.split(r'[-_]', feature)
    return ' '.join(w.capitalize() for w in words if w)


def main(argv=None):
    ap = argparse.ArgumentParser(description='创建 SDD 规范目录骨架')
    ap.add_argument('feature', nargs='?', help='feature 名(如 user-auth)')
    ap.add_argument('--root', default='.', help='项目根目录(默认当前目录)')
    ap.add_argument('--force', action='store_true', help='覆盖已存在的文件')
    ap.add_argument('--list', action='store_true', help='列出模板目录后退出')
    args = ap.parse_args(argv)

    if args.list:
        print('模板目录: %s' % TEMPLATE_DIR)
        for name in sorted(os.listdir(TEMPLATE_DIR)):
            print('  - %s' % name)
        return 0

    if not args.feature:
        ap.error('必须给出 feature 名')
    feature = args.feature.strip()
    if not re.match(r'^[A-Za-z0-9][A-Za-z0-9._-]*$', feature):
        sys.stderr.write('feature 名只允许字母、数字、点、下划线和连字符: %s\n' % feature)
        return 1

    root = os.path.abspath(args.root)
    target = os.path.join(root, 'specs', feature)
    os.makedirs(target, exist_ok=True)

    title = display_name(feature)
    today = datetime.date.today().isoformat()

    created, skipped = [], []
    for name in DOCS:
        src = os.path.join(TEMPLATE_DIR, name)
        dst = os.path.join(target, name)
        if not os.path.isfile(src):
            sys.stderr.write('模板缺失: %s\n' % src)
            return 2
        if os.path.exists(dst) and not args.force:
            skipped.append(name)
            continue
        with open(src, 'r', encoding='utf-8') as fh:
            body = fh.read()
        body = body.replace('<项目名称>', title)
        body = body.replace('<feature 名>', feature)
        body = body.replace('<YYYY-MM-DD>', today)
        with open(dst, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(body)
        created.append(name)

    print('规范目录: %s' % target)
    for name in created:
        print('  已创建 %s' % name)
    for name in skipped:
        print('  已存在,跳过 %s(加 --force 可覆盖)' % name)
    print('')
    print('下一步:按 assets/templates/ 里的说明填写三份文档,完成后运行')
    print('  python %s --feature %s'
          % (os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          'validate_specs.py'), feature))
    return 0


if __name__ == '__main__':
    sys.exit(main())

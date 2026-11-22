#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hook_probe.py — 问 hook「这条命令会不会被放行」

auto 模式下服务端分类器不可达时,命令被拒了却看不出**为什么**。这个脚本
直接调用 hook 的判定函数,把结论打出来 —— 纯本地,不碰网络,也不执行命令。

用法:

    python hook_probe.py "git status && rm -rf /"
    python hook_probe.py                 # 从 stdin 读一条命令
    python hook_probe.py --list          # 打印当前生效的白名单
    python hook_probe.py --hook <路径>    # 问**别的项目**里装的那份 hook
                                       # (白名单跟着那个项目走)
    python hook_probe.py --project <目录> # 以那个目录当「项目根」判定
                                       # (不给就从 --hook 的路径反推)
    python hook_probe.py --debug "<命令>"  # 逐段打印判定,看是哪一段过不了

默认探的是**项目里装的那份** hook(``<项目>/.claude/hooks/``)—— 白名单就装在它
旁边;源码目录旁边没有白名单文件,拿源码判出来的结论是假的。

输出是 JSON:

    {"command": "...", "allowed": false, "reason": null}
    {"command": "git status", "allowed": true, "reason": "git status"}

``reason`` 是放行时命中的那一条(命中理由),不放行时是 null。
"""

import json
import os
import sys

# Windows 控制台默认可能是 GBK,强制 UTF-8 输出避免中文乱码
for _stream in ('stdout', 'stderr'):
    try:
        getattr(sys, _stream).reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

HOOK_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'hooks')


def load_hook(path=None):
    """导入 hook 模块;``path`` 给了就用那一份(别的项目里装的)。"""
    if path:
        directory = os.path.dirname(os.path.abspath(path))
        if directory not in sys.path:
            sys.path.insert(0, directory)
    elif HOOK_DIR not in sys.path:
        sys.path.insert(0, HOOK_DIR)
    import sdd_bash_allow
    return sdd_bash_allow


def default_hook_path():
    """默认探**项目里装的那份** hook —— 白名单就装在它旁边。

    源码目录(``skills/sdd/hooks/``)旁边没有 ``sdd-hook-allow.json``,
    拿它来判会把「白名单里的命令」全判成不放行 —— 结论是假的。
    """
    project = os.environ.get('CLAUDE_PROJECT_DIR') or os.getcwd()
    candidate = os.path.join(project, '.claude', 'hooks', 'sdd_bash_allow.py')
    if os.path.isfile(candidate):
        return candidate
    return os.path.join(HOOK_DIR, 'sdd_bash_allow.py')


def infer_project(hook_path):
    """从 hook 的路径反推它装在哪个项目里:``<项目>/.claude/hooks/x.py``。"""
    if not hook_path:
        return ''
    hooks_dir = os.path.dirname(os.path.dirname(os.path.abspath(hook_path)))
    if os.path.basename(hooks_dir).lower() == '.claude':
        return os.path.dirname(hooks_dir)
    return ''


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    hook_path = None
    project = None
    if '--hook' in argv:
        i = argv.index('--hook')
        if i + 1 >= len(argv):
            sys.stderr.write('--hook 后面要给路径\n')
            return 2
        hook_path = argv[i + 1]
        del argv[i:i + 2]
    if '--project' in argv:
        i = argv.index('--project')
        if i + 1 >= len(argv):
            sys.stderr.write('--project 后面要给目录\n')
            return 2
        project = argv[i + 1]
        del argv[i:i + 2]
    # 项目根决定「路径在不在项目内」这件事 —— 探别的项目时必须切过去,
    # 否则同一个命令在两个项目里的结论会不一样。
    if not project:
        project = infer_project(hook_path)
    if project:
        os.environ['CLAUDE_PROJECT_DIR'] = os.path.abspath(project)
    if not hook_path:
        hook_path = default_hook_path()
    try:
        hook = load_hook(hook_path)
    except Exception as exc:  # pragma: no cover
        sys.stderr.write('导入 hook 失败: %s\n' % exc)
        return 1
    if '--list' in argv:
        extra, tools, write_paths = hook.load_allowlist()
        print(json.dumps({'extra': extra, 'tools': tools,
                          'write_paths': write_paths},
                         ensure_ascii=False, indent=2))
        return 0

    debug = '--debug' in argv
    if debug:
        argv.remove('--debug')
    if argv:
        command = ' '.join(argv)
    else:
        command = sys.stdin.read()

    reason = hook.evaluate(command)
    out = {'command': command, 'allowed': bool(reason), 'reason': reason}
    if debug:
        # 逐段打印判定 —— 「整条不放行」时能看出是哪一段过不了
        extra, _tools, _paths = hook.load_allowlist()
        segments, raws, writes, unterminated = hook.split_segments(command)
        base = os.getcwd()
        detail = []
        for tokens, raw in zip(segments, raws):
            if tokens and tokens[0].lower() == 'cd':
                # 新旧两版 hook 的 resolve_dir 签名不同(旧版收一个目录参数)
                if hasattr(hook, 'cd_target'):
                    base = hook.resolve_dir(tokens[1:], base)
                else:
                    base = hook.resolve_dir(tokens[1] if len(tokens) > 1 else '',
                                           base)
                detail.append({'segment': raw, 'reason': 'cd -> %s' % (base or '?')})
                continue
            hit = hook.evaluate_segment(tokens, extra, base, raw)
            detail.append({'segment': raw, 'reason': hit or '不放行'})
        out['segments'] = detail
        out['writes'] = writes
        out['unterminated'] = unterminated
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())

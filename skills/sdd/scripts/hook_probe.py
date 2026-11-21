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
if HOOK_DIR not in sys.path:
    sys.path.insert(0, HOOK_DIR)

try:
    import sdd_bash_allow as hook
except Exception as exc:  # pragma: no cover
    sys.stderr.write('导入 hook 失败: %s\n' % exc)
    sys.exit(1)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if '--list' in argv:
        extra, tools, write_paths = hook.load_allowlist()
        print(json.dumps({'extra': extra, 'tools': tools,
                          'write_paths': write_paths},
                         ensure_ascii=False, indent=2))
        return 0

    if argv:
        command = ' '.join(argv)
    else:
        command = sys.stdin.read()

    reason = hook.evaluate(command)
    print(json.dumps({'command': command, 'allowed': bool(reason),
                      'reason': reason}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())

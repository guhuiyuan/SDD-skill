#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
install_hook.py — 给项目装上 SDD 的 PreToolUse hook(auto 模式下的放行机制)

## 为什么

Claude Code 的 auto 模式靠**服务端安全分类器**代替用户回答权限问题。代理端点
不透传该调用时,分类器不可达 → **所有需要它判断的命令都被直接拒绝**,而且 auto
模式没有"回退到问用户"的路径。

PreToolUse hook 跑在**任何权限模式检查之前**,返回 ``permissionDecision: allow``
即可跳过权限检查 —— 纯本地机制,不依赖服务端分类器。hook 由 harness 直接执行,
不经权限系统,分类器不可达时照样能跑。

## 装什么

1. ``<root>/.claude/hooks/sdd-bash-allow.py``   —— hook 本体(从技能目录复制)
2. ``<root>/.claude/hooks/sdd-hook-allow.json`` —— 可编辑的白名单
3. ``<root>/.claude/settings.json`` 的 ``hooks.PreToolUse`` —— 注册 hook

只动 ``hooks.PreToolUse``,**不碰 settings.json 里已有的 permissions 等其他键**。
重复运行是幂等的。**不会删除用户自己注册的 hook** —— 同组里别人的 hook 原样留着。

## 验证命令白名单

给了 ``--feature`` 时,会从 ``specs/<feature>/tasks.md`` 的「验证命令」字段里
提取命令写进白名单 —— 这样阶段 4 跑测试、子 agent 跑测试都不会再撞分类器。

提取出来的命令会**逐条打印**给你看;含 shell 复合操作符(``&&`` ``;`` ``|`` 等)
的命令**一律不收** —— 白名单只放行"一条干净的命令",不能借它夹带别的动作。

## 用法

    python install_hook.py --root <项目根> [--feature <feature>]
                           [--extra "<命令前缀>"]... [--python <解释器>]
                           [--portable] [--dry-run] [--keep-existing]

    --portable   注册命令里用 ${CLAUDE_PROJECT_DIR} 而不是绝对路径。
                 只在**本项目的 .claude 目录就是 Claude Code 的项目目录**时用,
                 否则 hook 会找不到文件(绝对路径永远是对的,默认用它)。

退出码: 0 = 成功; 1 = 用法/配置错误
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

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE_HOOK = os.path.join(SKILL_DIR, 'hooks', 'sdd_bash_allow.py')
SOURCE_ALLOWLIST = os.path.join(SKILL_DIR, 'hooks', 'sdd-hook-allow.json')

HOOK_NAME = 'sdd_bash_allow.py'
ALLOWLIST_NAME = 'sdd-hook-allow.json'
HOOK_MARKER = 'sdd_bash_allow.py'

# tasks.md 里的「验证命令」字段:- **验证命令**: `命令`
# 校验器(validate_specs.py)的 field_value 不要求反引号,这里也要能吃下无反引号的写法
VERIFY_RE = re.compile(
    r'^\s*-\s*\*\*验证命令\*\*\s*[:：]\s*(?:`([^`]+)`|([^`\n]+?))\s*$', re.M)

# 白名单只放行"一条干净的命令" —— 带复合操作符的会被 hook 拒绝,提取时也直接跳过
UNSAFE_CMD_RE = re.compile(r'&&|\|\||[;|&><`]|\$\(')

# 剥掉开头的 `cd <路径> && `
CD_PREFIX_RE = re.compile(r'^\s*cd\s+(?:"[^"]*"|\'[^\']*\'|[^\s&|;]+)\s*&&\s*')


def read(path):
    if not os.path.isfile(path):
        return ''
    with open(path, 'r', encoding='utf-8') as fh:
        return fh.read()


def write_json(path, data):
    with open(path, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write('\n')


def load_json(path, default):
    if not os.path.isfile(path):
        return default, False
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
    except Exception:
        return default, False
    if not isinstance(data, type(default)):
        return default, False
    return data, True


def json_problem(path):
    """文件存在但不是合法 JSON 时返回错误说明,否则返回 None。

    这种情况下**拒绝覆盖** —— 用户/settings.json 里的东西不该被脚本悄悄抹掉。
    """
    problem, _ = read_json_checked(path)
    return problem


def read_json_checked(path):
    """读 JSON 并检查结构。返回 (错误说明, 解析出来的对象)。

    错误说明为 None 表示没问题;对象为 None 表示文件不存在。
    """
    if not os.path.isfile(path):
        return None, None
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
    except Exception as exc:
        return '%s 解析失败,拒绝覆盖: %s' % (path, exc), None
    return None, data


def shape_problem(path, data, spec):
    """结构不对时返回错误说明(而不是把用户的条目清空)。

    spec 是 {键名: 期望类型}。键不存在没问题;存在但类型不对 → 报错拒绝覆盖。
    """
    if not isinstance(data, dict):
        return '%s 的顶层不是对象' % path
    for key, want in spec.items():
        if key in data and not isinstance(data[key], want):
            return '%s 的 %r 应该是 %s,实际是 %s' % (
                path, key, want.__name__, type(data[key]).__name__)
    return None


def extract_verify_commands(tasks_text):
    """从 tasks.md 提取「验证命令」。

    返回 (可用的命令列表, 因含复合操作符被跳过的命令列表)。
    占位符、空命令不取。
    """
    out, skipped = [], []
    for m in VERIFY_RE.finditer(tasks_text):
        raw = (m.group(1) or m.group(2) or '').strip()
        if not raw or raw.startswith('<'):
            continue
        cmd = CD_PREFIX_RE.sub('', raw)
        if not cmd:
            continue
        if UNSAFE_CMD_RE.search(cmd):
            if cmd not in skipped:
                skipped.append(cmd)
            continue
        if cmd not in out:
            out.append(cmd)
    return out, skipped


def hook_entry(python_exe, root, portable=False):
    """构造 PreToolUse 条目。

    默认用**绝对路径** —— 它永远指向真实存在的文件。
    ``${CLAUDE_PROJECT_DIR}`` 只在 ``--portable`` 时用,而且只有当
    ``$CLAUDE_PROJECT_DIR`` 真的等于 root 时才成立;否则会指向不存在的文件
    (CLAUDE_PROJECT_DIR 是 Claude Code 的项目目录,跟当前工作目录不是一回事)。
    """
    hooks_dir = os.path.join(root, '.claude', 'hooks')
    if portable:
        project = os.environ.get('CLAUDE_PROJECT_DIR')
        if project and os.path.normcase(os.path.abspath(project)) == \
                os.path.normcase(os.path.abspath(root)):
            script = '${CLAUDE_PROJECT_DIR}/.claude/hooks/%s' % HOOK_NAME
        else:
            script = os.path.join(hooks_dir, HOOK_NAME).replace('\\', '/')
    else:
        script = os.path.join(hooks_dir, HOOK_NAME).replace('\\', '/')
    return {
        'matcher': 'Bash',
        'hooks': [{
            'type': 'command',
            # 解释器路径也要加引号 —— Windows 上默认装在 "C:\Program Files\..." 下
            'command': '"%s" "%s"' % (python_exe, script),
        }],
    }


def is_our_hook(h):
    return isinstance(h, dict) and HOOK_MARKER in str(h.get('command', ''))


def strip_our_entries(pre):
    """从 PreToolUse 列表里移除本工具注册的 hook。

    只摘掉**我们那一条 hook**,同一个 matcher 组里用户自己注册的 hook 原样留着;
    整组只剩我们的 hook 时,整组才去掉。
    """
    out = []
    for entry in pre:
        if not isinstance(entry, dict):
            out.append(entry)
            continue
        hooks = entry.get('hooks')
        if not isinstance(hooks, list):
            out.append(entry)
            continue
        kept = [h for h in hooks if not is_our_hook(h)]
        if len(kept) == len(hooks):
            out.append(entry)
            continue
        if kept:
            new_entry = dict(entry)
            new_entry['hooks'] = kept
            out.append(new_entry)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description='给项目装上 SDD 的 PreToolUse hook')
    ap.add_argument('--root', default='.', help='项目根目录(默认当前目录)')
    ap.add_argument('--feature', help='feature 名;给了就从它的 tasks.md 提取验证命令')
    ap.add_argument('--extra', action='append', default=[],
                    help='额外放行的命令前缀,可重复给;例如 --extra "pytest tests/"')
    ap.add_argument('--python', default='python', help='跑 hook 用的解释器(默认 python)')
    ap.add_argument('--portable', action='store_true',
                    help='注册命令里用 ${CLAUDE_PROJECT_DIR}(默认用绝对路径)')
    ap.add_argument('--dry-run', action='store_true', help='只打印将做的改动')
    ap.add_argument('--keep-existing', action='store_true',
                    help='已存在的 hook 文件不覆盖')
    args = ap.parse_args(argv)

    if not os.path.isfile(SOURCE_HOOK):
        sys.stderr.write('找不到 hook 源文件: %s\n' % SOURCE_HOOK)
        return 1

    root = os.path.abspath(os.path.expanduser(args.root))
    if not os.path.isdir(root):
        sys.stderr.write('--root 不是一个目录: %s\n' % root)
        return 1

    hooks_dir = os.path.join(root, '.claude', 'hooks')
    settings_path = os.path.join(root, '.claude', 'settings.json')
    hook_path = os.path.join(hooks_dir, HOOK_NAME)
    allowlist_path = os.path.join(hooks_dir, ALLOWLIST_NAME)

    actions = []
    added_cmds = []
    skipped_cmds = []

    # ---- 结构检查:不对就拒绝覆盖,别把用户的东西抹掉 ----
    problem = json_problem(settings_path)
    if problem:
        sys.stderr.write('%s\n' % problem)
        return 1
    problem = json_problem(allowlist_path)
    if problem:
        sys.stderr.write('%s\n' % problem)
        return 1
    if os.path.isfile(settings_path):
        _, settings_raw = read_json_checked(settings_path)
        problem = shape_problem(settings_path, settings_raw, {'hooks': dict})
        if problem:
            sys.stderr.write('%s\n' % problem)
            return 1
    if os.path.isfile(allowlist_path):
        _, allow_raw = read_json_checked(allowlist_path)
        problem = shape_problem(allowlist_path, allow_raw,
                                {'extra': list, 'tools': list})
        if problem:
            sys.stderr.write('%s\n' % problem)
            return 1

    # ---- 1. hook 本体 ----
    source_body = read(SOURCE_HOOK)
    existing = read(hook_path)
    if existing and args.keep_existing:
        actions.append('保留已存在的 %s' % hook_path)
    elif existing == source_body:
        actions.append('hook 文件已是最新: %s' % hook_path)
    else:
        actions.append(('更新' if existing else '创建') + ' hook: %s' % hook_path)

    # ---- 2. 白名单 ----
    # 以技能里带的模板为底(模板里的说明文字跟着走,用户看得懂格式)
    template, _ = load_json(SOURCE_ALLOWLIST, {'extra': [], 'tools': []})
    allowlist, existed = load_json(allowlist_path, template)
    allowlist.setdefault('extra', [])
    allowlist.setdefault('tools', [])
    # 说明文字是生成的(不是用户内容),每次都从模板刷新
    if '_说明' in template:
        allowlist['_说明'] = template['_说明']

    for cmd in args.extra:
        cmd = CD_PREFIX_RE.sub('', str(cmd).strip())
        if cmd and cmd not in allowlist['extra']:
            allowlist['extra'].append(cmd)
            added_cmds.append(cmd)
    if args.feature:
        tasks_path = os.path.join(root, 'specs', args.feature, 'tasks.md')
        if not os.path.isfile(tasks_path):
            sys.stderr.write('找不到 %s,跳过验证命令提取\n' % tasks_path)
        else:
            cmds, skipped = extract_verify_commands(read(tasks_path))
            for cmd in cmds:
                if cmd not in allowlist['extra']:
                    allowlist['extra'].append(cmd)
                    added_cmds.append(cmd)
            skipped_cmds.extend(s for s in skipped if s not in skipped_cmds)
            if not cmds:
                sys.stderr.write('从 %s 里没提取到任何验证命令'
                                 '(「验证命令」字段写成 `- **验证命令**: `命令``)\n'
                                 % tasks_path)

    # ---- 3. 注册到 settings.json ----
    settings, settings_existed = load_json(settings_path, {})
    if not isinstance(settings.get('hooks'), dict):
        settings['hooks'] = {}
    pre = settings['hooks'].get('PreToolUse')
    if not isinstance(pre, list):
        pre = []
    pre = strip_our_entries(pre)
    pre.append(hook_entry(args.python, root, args.portable))
    settings['hooks']['PreToolUse'] = pre
    if not settings_existed:
        actions.append('创建 settings.json: %s' % settings_path)

    if args.dry_run:
        print('(dry-run,未写入)将执行:')
        for a in actions:
            print('  - %s' % a)
        print('')
        print('settings.json 的 hooks.PreToolUse 将变成:')
        print(json.dumps(settings['hooks'], ensure_ascii=False, indent=2))
        print('')
        print('白名单将变成:')
        print(json.dumps(allowlist, ensure_ascii=False, indent=2))
        return 0

    # ---- 4. 落盘 ----
    os.makedirs(hooks_dir, exist_ok=True)
    if not (existing and args.keep_existing) and existing != source_body:
        with open(hook_path, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(source_body)
    write_json(allowlist_path, allowlist)
    write_json(settings_path, settings)

    print('已安装 SDD 的 PreToolUse hook:')
    for a in actions:
        print('  - %s' % a)
    print('  - 写入 %s' % allowlist_path)
    print('  - 写入 %s 的 hooks.PreToolUse' % settings_path)
    print('')
    if added_cmds:
        print('放行的额外命令(逐条列出,你可以核对):')
        for cmd in added_cmds:
            print('  + %s' % cmd)
    else:
        print('放行的额外命令:无(只有内置的测试命令)')
    if skipped_cmds:
        print('')
        print('因含 shell 复合操作符而**未**放行的命令:')
        for cmd in skipped_cmds:
            print('  ! %s' % cmd)
        print('  (白名单只放行"一条干净的命令",复合命令请自己改成单条再跑)')
    print('')
    print('放行范围:')
    print('  - SDD 技能自己的脚本(python + skills/sdd/scripts/*.py 或 .claude/sdd/*.py)')
    print('  - git 的安全子命令(status/diff/log/show/rev-parse/branch/add/commit)')
    print('  - 白名单里的额外命令(%s)' % ALLOWLIST_NAME)
    print('')
    print('不放行 rm / curl / git push / 任意 python;带 && ; | 等复合操作符的命令也不放行。')
    print('本 hook 的 allow 不能覆盖 permissions.deny / ask 规则 —— deny/ask 优先。')
    print('你自己注册的其它 hook 不会被删。')
    print('')
    print('下一步:**新开一个会话**让 hook 生效(Claude Code 在会话启动时读取 settings.json),')
    print('      然后在 auto 模式下试跑一条 SDD 脚本命令,确认不再被拒。')
    return 0


if __name__ == '__main__':
    sys.exit(main())

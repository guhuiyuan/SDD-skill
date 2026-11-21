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

注册**三组** matcher:

- ``Bash`` —— 按命令白名单放行(SDD 脚本 / git 安全子命令 / extra 列表)
- ``Write|Edit|MultiEdit|NotebookEdit`` —— 按**路径**放行:目标在项目目录内就放行,
  项目内的 ``.claude/`` 与 ``.git/`` 仍然不放行(改权限、改 git 配置留给人手动做)
- ``Agent|Task`` —— 放行"派发子 agent"这一步(auto 模式下分类器不可达时,
  连派发都会被拒,SDD 的并行实现就没法用)。子 agent **内部**跑命令仍走同一份
  白名单 —— 它继承的是会话级设置,hook 照样管得住。

只动 ``hooks.PreToolUse``,**不碰 settings.json 里已有的 permissions 等其他键**。
重复运行是幂等的。**不会删除用户自己注册的 hook** —— 同组里别人的 hook 原样留着。

## 验证命令白名单

给了 ``--feature`` 时,会从 ``specs/<feature>/tasks.md`` 的「验证命令」字段里
提取命令写进白名单 —— 这样阶段 4 跑测试、子 agent 跑测试都不会再撞分类器。

提取出来的命令会**逐条打印**给你看。含 shell 复合操作符(``&&`` ``;`` ``|``)的命令
会**按段拆开**逐段收 —— hook 判定复合命令时也是逐段判的,每段都要单独命中白名单。
只要有一段不安全(危险可执行文件、重定向、后台运行、命令替换),整条都不收。

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

# 写文件类工具的 matcher —— Claude Code 的 matcher 是正则,一次覆盖四个工具。
# 放行与否只看路径(项目目录内),不看工具名。
WRITE_MATCHER = 'Write|Edit|MultiEdit|NotebookEdit'

# 派发子 agent 的 matcher —— 放行"派发"这一步。
# 子 agent 内部跑命令仍走同一份白名单,其他工具仍要服务端分类器。
AGENT_MATCHER = 'Agent|Task'

# tasks.md 里的「验证命令」字段:- **验证命令**: `命令`
# 校验器(validate_specs.py)的 field_value 不要求反引号,这里也要能吃下无反引号的写法。
# 字段里常常有多条命令(`` `cmd1` 与 `cmd2` ``)—— 反引号是 markdown 代码段,
# **不是** shell 的命令替换,所以要按代码段拆开,不能拿整段文本去查 `$(`。
# 字段还常跨行(续行是缩进的):
#     - **验证命令**: `cmd1` 与
#       `cmd2`
VERIFY_LINE_RE = re.compile(
    r'^\s*-\s*\*\*验证命令\*\*\s*[:：]\s*(.+?)\s*$')
CODE_SPAN_RE = re.compile(r'`([^`]+)`')

# 白名单只放行"一条干净的命令" —— 带复合操作符的要按段拆开逐段收
UNSAFE_CMD_RE = re.compile(r'&&|\|\||[;|&><`]|\$\(')

# 剥掉开头的 `cd <路径> && `
CD_PREFIX_RE = re.compile(r'^\s*cd\s+(?:"[^"]*"|\'[^\']*\'|[^\s&|;]+)\s*&&\s*')

# hook 模块(懒加载)—— 复用它的拆段与安全判定,免得两处实现对不上
_HOOK_MODULE = None


def hook_module():
    """导入 hook 模块;导入不了就返回 None(这时只收单条命令)。"""
    global _HOOK_MODULE
    if _HOOK_MODULE is None:
        hooks_dir = os.path.join(SKILL_DIR, 'hooks')
        if hooks_dir not in sys.path:
            sys.path.insert(0, hooks_dir)
        try:
            import sdd_bash_allow
            _HOOK_MODULE = sdd_bash_allow
        except Exception:
            _HOOK_MODULE = False
    return _HOOK_MODULE or None


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


def split_command(cmd):
    """把一条命令拆成「可以进白名单的段」。

    返回 (段列表, 是否安全)。
    - 复合命令按 hook 的规则拆段(引号外的 && ; | 与换行)
    - ``cd`` 段直接丢掉(只是换目录,hook 也跳过它)
    - 带重定向 / 后台运行 / 命令替换 → 整条不安全
    - 段里是危险可执行文件(rm / curl / sh / npx …)或太松的模式 → 不安全
    """
    hook = hook_module()
    if hook is None:
        return [], False
    if hook.has_substitution(cmd):
        return [], False
    segments, raws, writes, unterminated = hook.split_segments(cmd)
    if writes or unterminated:
        return [], False
    out = []
    for tokens, raw in zip(segments, raws):
        if not tokens:
            continue
        if tokens[0].lower() == 'cd':
            continue
        exe = os.path.basename(tokens[0]).lower()
        if exe.endswith('.exe'):
            exe = exe[:-4]
        if exe in hook.DENIED_EXECUTABLES:
            return [], False
        if hook.pattern_is_loose(raw):
            return [], False
        out.append(raw)
    return out, True


def iter_verify_fields(tasks_text):
    """产出每个「验证命令」字段的完整文本(含缩进续行)。

    字段常跨行:
        - **验证命令**: `cmd1` 与
          `cmd2`
    续行必须一起取,否则第二条命令会被漏掉。
    """
    lines = tasks_text.splitlines()
    for i, line in enumerate(lines):
        m = VERIFY_LINE_RE.match(line)
        if not m:
            continue
        parts = [m.group(1).strip()]
        for nxt in lines[i + 1:]:
            if not nxt.strip() or not nxt.startswith((' ', '\t')):
                break
            parts.append(nxt.strip())
        yield ' '.join(parts)


def extract_verify_commands(tasks_text):
    """从 tasks.md 提取「验证命令」。

    返回 (可用的命令列表, 因不安全被跳过的命令列表)。
    占位符、空命令不取。一个字段里可能有多条命令(`` `cmd1` 与 `cmd2` ``),
    逐条处理。复合命令按段拆开 —— 只要有一段不安全,整条都不收
    (hook 判定复合命令也是逐段判的,放行了半条链没有意义)。
    """
    out, skipped = [], []
    hook = hook_module()
    for raw in iter_verify_fields(tasks_text):
        if not raw or raw.startswith('<'):
            continue
        spans = [s.strip() for s in CODE_SPAN_RE.findall(raw) if s.strip()]
        if spans:
            # 代码段里也可能混着说明(「外加一次核对:`GET /api/x`」)——
            # 不像命令的片段剔掉,别把路径和接口登记成命令
            cmds = [s for s in spans
                    if hook is None or hook.looks_like_command(s)]
        else:
            cmds = [raw]
        for one in cmds:
            if not one or one.startswith('<'):
                continue
            cmd = CD_PREFIX_RE.sub('', one)
            if not cmd:
                continue
            if UNSAFE_CMD_RE.search(cmd):
                pieces, safe = split_command(cmd)
                if safe and pieces:
                    for piece in pieces:
                        if piece not in out:
                            out.append(piece)
                elif cmd not in skipped:
                    skipped.append(cmd)
                continue
            if cmd not in out:
                out.append(cmd)
    return out, skipped


def hook_entry(python_exe, root, portable=False):
    """构造 Bash 的 PreToolUse 条目(保持原样,便于兼容既有调用方)。"""
    return _hook_entry('Bash', python_exe, root, portable)


def _hook_entry(matcher, python_exe, root, portable=False):
    """构造一条 PreToolUse 条目。

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
        'matcher': matcher,
        'hooks': [{
            'type': 'command',
            # 解释器路径也要加引号 —— Windows 上默认装在 "C:\Program Files\..." 下
            'command': '"%s" "%s"' % (python_exe, script),
        }],
    }


def hook_entries(python_exe, root, portable=False):
    """本工具注册的全部 PreToolUse 条目。

    三组:Bash(按命令白名单放行)+ 写文件工具(按路径放行)+ 派发子 agent。
    Claude Code 的 matcher 支持正则,所以后两组一次覆盖多个工具。
    """
    return [_hook_entry('Bash', python_exe, root, portable),
            _hook_entry(WRITE_MATCHER, python_exe, root, portable),
            _hook_entry(AGENT_MATCHER, python_exe, root, portable)]


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
                                {'extra': list, 'tools': list,
                                 'write_paths': list})
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
    template, _ = load_json(SOURCE_ALLOWLIST, {'extra': [], 'tools': [],
                                                'write_paths': []})
    allowlist, existed = load_json(allowlist_path, template)
    allowlist.setdefault('extra', [])
    allowlist.setdefault('tools', [])
    allowlist.setdefault('write_paths', [])
    # 说明文字是生成的(不是用户内容),每次都从模板刷新
    if '_说明' in template:
        allowlist['_说明'] = template['_说明']

    for cmd in args.extra:
        cmd = CD_PREFIX_RE.sub('', str(cmd).strip())
        if not cmd:
            continue
        pieces, safe = split_command(cmd)
        if not safe:
            if cmd not in skipped_cmds:
                skipped_cmds.append(cmd)
            continue
        for piece in pieces:
            if piece not in allowlist['extra']:
                allowlist['extra'].append(piece)
                added_cmds.append(piece)
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
    pre.extend(hook_entries(args.python, root, args.portable))
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
        print('因含不安全成分而**未**放行的命令:')
        for cmd in skipped_cmds:
            print('  ! %s' % cmd)
        print('  (复合命令会按段拆开逐段收;但只要有一段危险/带重定向/带命令替换,')
        print('   整条都不收 —— 请改成单条再跑)')
    print('')
    print('放行范围:')
    print('  - SDD 技能自己的脚本(python + skills/sdd/scripts/*.py 或 .claude/sdd/*.py)')
    print('  - git 的安全子命令(status/diff/log/show/rev-parse/branch/add/commit)')
    print('  - 白名单里的额外命令(%s)' % ALLOWLIST_NAME)
    print('  - 只读命令(ls/cat/head/grep/find/diff/…):路径必须在项目目录内')
    print('  - 写文件工具(Write/Edit/MultiEdit/NotebookEdit):**目标在项目目录内**')
    print('    (项目内的 .claude/ 与 .git/ 仍然不放行 —— 改权限和改 git 配置留给人手动做)')
    print('  - 派发子 agent(Agent/Task):只放行"派发"这一步,')
    print('    子 agent 内部跑命令仍走同一份白名单')
    if allowlist.get('write_paths'):
        print('    只限这些子路径: %s' % ', '.join(allowlist['write_paths']))
    print('')
    print('不放行 rm / curl / git push / 任意 python;')
    print('也不放行 shell 与脚本解释器(sh / bash / cmd / powershell / node / npx …)、')
    print('包装器(env / xargs)、find -exec、文件重定向(> >> <)、后台运行(&)、')
    print('以及任何位置的命令替换。')
    print('复合命令(&& ; |)按段判定:每段都要单独命中白名单,全过才整条放行。')
    print('项目目录外的文件写入不放行;路径里带 .claude 或 .git 的写入也不放行。')
    print('本 hook 的 allow 不能覆盖 permissions.deny / ask 规则 —— deny/ask 优先。')
    print('你自己注册的其它 hook 不会被删。')
    print('')
    print('下一步:试跑一条 SDD 脚本命令,确认不再被拒。')
    print('      (实测 harness 会重读 settings.json,通常不用重开会话;没生效再新开一个。)')
    return 0


if __name__ == '__main__':
    sys.exit(main())

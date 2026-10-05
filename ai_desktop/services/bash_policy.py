"""Bash syntax validation and a deliberately small literal-command grammar.

This is not a full Bash parser. Anything outside this proven subset needs an
exact-command confirmation; shlex splitting never acts as authorization.
"""
import re
import stat
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from ai_desktop.services.execution_context import BashPolicy, ExecutionSnapshot


class CommandDisposition(str, Enum):
    AUTO = 'auto'
    CONFIRM = 'confirm'
    INVALID = 'invalid'


@dataclass(frozen=True)
class CommandDecision:
    disposition: CommandDisposition
    reason: str
    argv: tuple[str, ...] = ()


_PROGRAMS = {'pwd':'/bin/pwd', 'ls':'/bin/ls', 'cat':'/bin/cat', 'head':'/usr/bin/head',
             'tail':'/usr/bin/tail', 'wc':'/usr/bin/wc'}
_ASSIGNMENT = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=')


def literal_words(command):
    """Return exact words only for literals, quoting and simple whitespace.

    Double-quote backslashes follow Bash's restricted escape semantics. Shell
    control/expansion/comment syntax is rejected from the automatic subset.
    """
    words, word = [], []
    quote = None
    started = False
    i = 0
    while i < len(command):
        char = command[i]
        if quote == "'":
            if char == "'":
                quote = None
            else:
                word.append(char)
        elif quote == '"':
            if char == '"':
                quote = None
            elif char in '$`\n':
                return None
            elif char == '\\':
                i += 1
                if i >= len(command):
                    return None
                next_char = command[i]
                if next_char in '$`"\\':
                    word.append(next_char)
                elif next_char == '\n':
                    return None
                else:
                    word.extend(('\\', next_char))
            else:
                word.append(char)
        elif char in "'\"":
            quote, started = char, True
        elif char == '\\':
            i += 1
            if i >= len(command) or command[i] == '\n':
                return None
            word.append(command[i])
            started = True
        elif char in ' \t':
            if started:
                words.append(''.join(word))
                word, started = [], False
        elif char in '\n\r$`|&;<>()*?[]{}' or (char == '~' and not started) or (char == '#' and not started):
            return None
        else:
            word.append(char)
            started = True
        i += 1
    if quote is not None:
        return None
    if started:
        words.append(''.join(word))
    if words and _ASSIGNMENT.match(words[0]):
        return None
    return words


def _reason(command, words):
    name = Path(words[0]).name if words else ''
    if name in {'sudo', 'su', 'doas', 'launchctl'}:
        return '检测到提权或系统控制命令。'
    if name in {'curl', 'wget', 'nc', 'ssh', 'scp', 'rsync'}:
        return '检测到网络命令；联网检索优先使用搜索工具。'
    if name in {'rm', 'mv', 'cp', 'chmod', 'dd', 'ln', 'truncate', 'mkdir', 'touch', 'tee', 'git'}:
        return '命令可能写入或修改文件。'
    if name in {'python', 'python3', 'node', 'ruby', 'perl', 'sh', 'bash', 'xargs', 'make', 'npm'}:
        return '检测到脚本或动态执行命令。'
    if words is None:
        return '命令包含控制、展开、重定向或其他超出自动判定范围的语法。'
    return '命令或选项不在已验证的只读允许列表中。'


def _literal_argv(words, snapshot):
    if not words:
        return (), '命令没有可执行内容。'
    name = next((name for name, fixed in _PROGRAMS.items() if words[0] in {name, fixed}), None)
    if name is None:
        return (), ''
    args, operands, options_done, explicit_end = [], [], False, False
    i = 1
    while i < len(words):
        value = words[i]
        if not options_done and value == '--':
            args.append('--')
            options_done, explicit_end = True, True
        elif not options_done and value.startswith('-'):
            if name == 'ls' and len(value) > 1 and all(c in 'laAh1' for c in value[1:]):
                args.append(value)
            elif name == 'wc' and len(value) > 1 and all(c in 'lwc' for c in value[1:]):
                args.append(value)
            elif name in {'head', 'tail'} and value == '-n':
                i += 1
                if i >= len(words) or len(words[i]) > 4 or not words[i].isdigit() or not 1 <= int(words[i]) <= 1000:
                    return (), '行数参数不在自动执行范围内。'
                args.extend(('-n', words[i]))
            else:
                return (), '存在未知选项或 stdin/follow 参数。'
        else:
            if value.startswith("-") and not explicit_end:
                return (), "文件之后的选项不在自动判定范围内。"
            options_done = True
            operands.append(value)
        i += 1
    if name == 'pwd':
        return ((_PROGRAMS[name],), '') if len(words) == 1 else ((), 'pwd 只允许无参数形式。')
    if name != 'ls' and not operands:
        return (), '只读文件命令必须指定现有文件，不从 stdin 等待。'
    root = Path(snapshot.workspace)
    resolved = []
    for operand in operands:
        if not operand or operand == '-':
            return (), '不自动读取 stdin 或空路径。'
        target = Path(operand)
        if not target.is_absolute():
            target = root / target
        try:
            target = target.resolve(strict=True)
            target.relative_to(root)
            mode = target.stat().st_mode
        except ValueError:
            return (), '文件路径或符号链接指向工作区之外。'
        except (OSError, RuntimeError):
            return (), '目标路径无法确定或不存在。'
        if not stat.S_ISREG(mode) and not (name == 'ls' and stat.S_ISDIR(mode)):
            return (), '目标不是允许读取的常规文件或目录。'
        resolved.append(str(target))
    return (_PROGRAMS[name], *args, *resolved), ''



def _background_operator(command):
    # Here-doc bodies are not parsed by this small grammar. Their ampersands
    # may be literal data, so leave such scripts in the confirmation branch.
    if "<<" in command:
        return False
    quote = None
    i = 0
    while i < len(command):
        char = command[i]
        if char == '\\' and quote != "'":
            i += 2
            continue
        if quote:
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
        elif char == '#' and (i == 0 or command[i-1].isspace()):
            end = command.find('\n', i)
            if end < 0:
                return False
            i = end
            continue
        elif char == '&':
            if i > 0 and command[i-1] in '<>|':
                i += 1
                continue
            if command[i:i+2] in {'&&', '&>'}:
                i += 2
                continue
            return True
        i += 1
    return False

def classify_command(command, snapshot: ExecutionSnapshot):
    if (not isinstance(command, str) or not command.strip() or len(command) > 16000
            or any(ord(c) < 32 and c not in '\n\t' for c in command)):
        return CommandDecision(CommandDisposition.INVALID, '命令为空、过长或含无效控制字符。')
    if not snapshot.valid():
        return CommandDecision(CommandDisposition.INVALID, '工作区已失效或被替换，请重新选择。')
    try:
        # -n parses without executing. Controlled env excludes BASH_ENV even
        # for this validation process; no user rc/profile is loaded.
        parsed = subprocess.run(['/bin/bash', '--noprofile', '--norc', '-n', '-c', command],
                                cwd=snapshot.workspace, env=snapshot.environment(), stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        return CommandDecision(CommandDisposition.INVALID, '无法完成 Bash 语法检查。')
    if parsed.returncode != 0:
        return CommandDecision(CommandDisposition.INVALID, 'Bash 语法无效，请修正命令。')
    if _background_operator(command):
        return CommandDecision(CommandDisposition.INVALID, "首版不支持显式后台任务。")
    words = literal_words(command)
    if snapshot.policy == BashPolicy.CONFIRM_ALL:
        return CommandDecision(CommandDisposition.CONFIRM, '工作区设置要求逐次确认有效命令。')
    if words is not None:
        argv, reason = _literal_argv(words, snapshot)
        if argv:
            return CommandDecision(CommandDisposition.AUTO, '工作区内的已验证只读命令。', tuple(argv))
        return CommandDecision(CommandDisposition.CONFIRM, reason or _reason(command, words))
    return CommandDecision(CommandDisposition.CONFIRM, _reason(command, words))

"""Web 终端命令执行：仅管理员使用，带命令白名单和危险命令拦截。"""
import locale
import os
import re
import subprocess
import sys

COMMAND_TIMEOUT = 30
MAX_COMMAND_LENGTH = 500
MAX_OUTPUT_CHARS = 100_000

ALLOWED_COMMANDS = {
    "cd", "clear", "cls", "pwd", "ls", "dir", "df", "du", "free", "ps",
    "uptime", "whoami", "uname", "hostname", "cat", "tail", "head", "grep",
    "find", "echo", "date", "env", "python", "python3", "pip", "pip3",
    "node", "npm", "docker", "docker-compose", "git", "curl", "wget",
    "netstat", "ss", "ip", "ipconfig", "systeminfo", "tasklist", "mysql",
    "redis-cli", "manage.py",
}

DENY_PATTERNS = [
    # 提权与账号切换
    re.compile(r"(^|[;&|\n]\s*)(sudo|su|doas)\b"),
    # 文件权限/属主修改
    re.compile(r"(^|[;&|\n]\s*)(chmod|chown|chattr)\b"),
    # 破坏性文件操作
    re.compile(r"(^|[;&|\n]\s*)(rm|rmdir|mv|shred|truncate)\b"),
    # 磁盘/分区/格式化
    re.compile(r"(^|[;&|\n]\s*)(mkfs|fdisk|gdisk|sfdisk|parted|dd)\b"),
    # 系统开关机与进程终止
    re.compile(r"(^|[;&|\n]\s*)(shutdown|reboot|halt|poweroff|init|kill|pkill|killall|systemctl)\b"),
    # 直接调用其他 shell
    re.compile(r"(^|[;&|\n]\s*)(bash|sh|zsh|pwsh|powershell|cmd)\b"),
    # fork 炸弹
    re.compile(r":\s*\(\s*\)\s*\{"),
    # 下载后直接执行
    re.compile(r"(curl|wget)[^|]*\|\s*(sh|bash|zsh|pwsh|powershell|cmd)\b"),
    # 解释器参数直接执行代码
    re.compile(r"(powershell|pwsh|cmd|bash|sh|zsh|python[0-9.]*|perl|ruby|node)\s+(-c|-command|/c|-e|--eval)\b"),
    re.compile(r"python[0-9.]*\s+-(c|i)\b"),
    # find 执行/删除
    re.compile(r"find[^|;]*\s+(-delete|-exec|-ok)\b"),
    # xargs 执行危险命令
    re.compile(r"xargs\s+(rm|mv|sh|bash|zsh|sudo|rmdir|shred|truncate)\b"),
    # 覆盖系统关键路径
    re.compile(r">\s*(/dev/|/etc/|/boot/|/proc/|/sys/|c:\\windows)"),
    # Git 破坏性操作
    re.compile(r"git\s+(clean|reset\s+--hard|push\s+(-f|--force)|checkout\s+--\s+\.|stash\s+drop)\b"),
    # Docker 破坏性操作
    re.compile(r"docker(\s+compose)?\s+(rm|rmi|run|exec|attach|build|volume\s+rm|system\s+prune)\b"),
    re.compile(r"docker(\s+compose)?\s+down\s+(-v|--volumes)\b"),
    re.compile(r"docker-compose\s+(rm|rmi|run|exec|attach|build|down\s+-v)\b"),
    # 数据库破坏性操作
    re.compile(r"mysql[^|;]*(drop\s+database|drop\s+table|truncate\s+table)"),
    re.compile(r"redis-cli[^|;]*\s+(flushall|flushdb|shutdown|config|debug)"),
    # Django 交互式 shell
    re.compile(r"manage\.py\s+(shell|dbshell)\b"),
]


def _first_token(command):
    return command.split(None, 1)[0].lower() if command.strip() else ""


def validate_command(command):
    """校验命令是否在允许范围内，返回 (是否允许, 拒绝原因)。"""
    command = (command or "").strip()
    if not command:
        return False, "命令不能为空"
    if len(command) > MAX_COMMAND_LENGTH:
        return False, f"命令过长（最多 {MAX_COMMAND_LENGTH} 字符）"
    if "\n" in command or "\r" in command:
        return False, "不支持多行命令"
    if "$(" in command or "`" in command:
        return False, "不支持命令替换"

    first = _first_token(command)
    if first not in ALLOWED_COMMANDS:
        return False, f"命令“{first}”不在允许范围内"

    lowered = command.lower()
    for pattern in DENY_PATTERNS:
        if pattern.search(lowered):
            return False, "该命令包含被禁止的危险操作"
    return True, ""


def _decode_output(data):
    if not data:
        return ""
    encodings = ["utf-8", locale.getpreferredencoding(False) or "utf-8"]
    for encoding in encodings:
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def _resolve_cd(cwd, target):
    target = (target or "").strip()
    if not target:
        return cwd
    if sys.platform == "win32" and target.lower().startswith("/d "):
        target = target[3:].strip()
    if len(target) >= 2 and target[0] == target[-1] and target[0] in ('"', "'"):
        target = target[1:-1].strip()
    if target == "~":
        target = os.path.expanduser("~")
    elif target.startswith("~/"):
        target = os.path.join(os.path.expanduser("~"), target[2:])
    new_cwd = os.path.abspath(os.path.join(cwd, target))
    if not os.path.isdir(new_cwd):
        raise ValueError(f"目录不存在：{new_cwd}")
    return new_cwd


def execute_terminal_command(command, cwd):
    """执行一条已校验命令，返回 {output, exit_code, cwd, clear?}。"""
    stripped = (command or "").strip()
    first = _first_token(stripped)
    if first in ("clear", "cls"):
        return {"output": "", "exit_code": 0, "cwd": cwd, "clear": True}
    if first == "cd":
        try:
            new_cwd = _resolve_cd(cwd, stripped[2:])
        except ValueError as exc:
            return {"output": str(exc), "exit_code": 1, "cwd": cwd}
        return {"output": new_cwd, "exit_code": 0, "cwd": new_cwd}

    proc = subprocess.run(
        stripped,
        shell=True,
        cwd=cwd,
        capture_output=True,
        timeout=COMMAND_TIMEOUT,
    )
    output = (_decode_output(proc.stdout) + _decode_output(proc.stderr)).strip("\n")
    if len(output) > MAX_OUTPUT_CHARS:
        output = output[:MAX_OUTPUT_CHARS] + "\n...（输出过长已截断）"
    return {"output": output, "exit_code": proc.returncode, "cwd": cwd}

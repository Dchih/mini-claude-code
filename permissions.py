"""
权限系统模块

为工具调用提供风险分级和用户确认机制。
- 安全操作：自动执行，无需确认
- 危险操作：暂停并询问用户，支持单次/会话级授权
"""

import json
import shlex
from enum import Enum
from typing import Optional
from prompt_toolkit import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.controls import FormattedTextControl


# ──────────────────────────────────────────────
# 风险等级定义
# ──────────────────────────────────────────────

class RiskLevel(str, Enum):
  SAFE = "safe"           # 无需确认
  DANGEROUS = "dangerous" # 需要用户确认


# ──────────────────────────────────────────────
# Git 只读命令白名单
# ──────────────────────────────────────────────

GIT_SAFE_SUBCOMMANDS = {
  "log", "status", "diff", "show", "branch", "tag",
  "remote", "stash list", "blame", "shortlog", "describe",
  "rev-parse", "ls-files", "ls-tree", "whatchanged",
  "reflog",
}

# 这些前缀开头的 git 命令也视为只读
GIT_SAFE_PREFIXES = (
  "log ", "status", "diff ", "show ", "branch ",
  "tag -l", "remote -v", "stash list", "blame ",
  "rev-parse", "ls-files", "ls-tree",
)


def _extract_git_subcommand(command: str) -> str:
  """从 git 命令中提取子命令（第一个非 flag token）"""
  # 去掉 git 前缀和 -C / --no-pager 等 flag
  tokens = command.strip().split()
  subcmd = None
  for t in tokens:
    if t == "git":
      continue
    if t.startswith("-"):
      continue
    if subcmd is None and t not in ("git",):
      subcmd = t
      break
  return subcmd or ""


# ──────────────────────────────────────────────
# 风险评估
# ──────────────────────────────────────────────

def assess_risk(tool_name: str, args: dict) -> RiskLevel:
  """
  评估工具调用的风险等级。
  
  Args:
    tool_name: 工具名称，如 "bash", "write_file", "git" 等
    args: 工具参数字典
    
  Returns:
    RiskLevel.SAFE 或 RiskLevel.DANGEROUS
  """
  # read_file 始终安全
  if tool_name == "read_file":
    return RiskLevel.SAFE

  # todo 始终安全（只是状态管理，不涉及文件/命令操作）
  if tool_name == "todo":
    return RiskLevel.SAFE

  # write_file / edit_file 始终危险
  if tool_name in ("write_file", "edit_file"):
    return RiskLevel.DANGEROUS

  # bash 始终危险（可执行任意命令）
  if tool_name == "bash":
    return RiskLevel.DANGEROUS

  # git 需要看子命令
  if tool_name == "git":
    command = args.get("command", "")
    subcmd = _extract_git_subcommand(command)

    # 检查是否在白名单中
    if subcmd in GIT_SAFE_SUBCOMMANDS:
      return RiskLevel.SAFE

    # 检查是否匹配安全前缀
    for prefix in GIT_SAFE_PREFIXES:
      if command.strip().startswith(prefix) or f"git {prefix}".strip() in command:
        return RiskLevel.SAFE

    # 其余 git 命令视为危险
    return RiskLevel.DANGEROUS

  # 未知工具默认危险
  return RiskLevel.DANGEROUS


# ──────────────────────────────────────────────
# 会话级授权记录
# ──────────────────────────────────────────────

class PermissionStore:
  """
  管理会话级权限授权。
  
  当用户选择 "始终允许" 后，同类型的操作在本次会话中不再询问。
  """

  def __init__(self):
    # key 是 (tool_name, risk_key)，value 是 True
    # risk_key 用于更细粒度的控制，如 git 的子命令
    self._allowed: dict[tuple[str, str], bool] = {}

  def is_allowed(self, tool_name: str, risk_key: str = "*") -> bool:
    """检查是否已有会话级授权"""
    # 先检查精确匹配
    if (tool_name, risk_key) in self._allowed:
      return True
    # 再检查通配符
    if (tool_name, "*") in self._allowed:
      return True
    return False

  def grant(self, tool_name: str, risk_key: str = "*"):
    """授予会话级权限"""
    self._allowed[(tool_name, risk_key)] = True

  def revoke_all(self):
    """撤销所有会话级权限"""
    self._allowed.clear()

  def revoke(self, tool_name: str, risk_key: str = "*"):
    """撤销特定权限"""
    self._allowed.pop((tool_name, risk_key), None)


# ──────────────────────────────────────────────
# 操作摘要生成
# ──────────────────────────────────────────────

def _truncate(text: str, max_len: int = 200) -> str:
  """截断长文本"""
  if len(text) <= max_len:
    return text
  return text[:max_len] + "…"


def build_summary(tool_name: str, args: dict) -> str:
  """生成操作摘要，用于权限确认提示"""
  if tool_name == "write_file":
    path = args.get("path", "?")
    content = args.get("content", "")
    return f"写入文件: {path} ({len(content)} 字符)"

  if tool_name == "edit_file":
    path = args.get("path", "?")
    old_text = _truncate(args.get("old_text", ""), 60)
    new_text = _truncate(args.get("new_text", ""), 60)
    return f"编辑文件: {path}\n    替换: {old_text}\n    替换为: {new_text}"

  if tool_name == "bash":
    command = _truncate(args.get("command", ""), 120)
    return f"执行命令: {command}"

  if tool_name == "git":
    command = _truncate(args.get("command", ""), 120)
    return f"Git 操作: git {command}"

  return f"{tool_name}({json.dumps(args, ensure_ascii=False)[:100]})"


def build_detail(tool_name: str, args: dict) -> str:
  """生成操作详情，用于用户按 v 查看时展示"""
  if tool_name == "write_file":
    path = args.get("path", "?")
    content = args.get("content", "")
    return f"📄 文件: {path}\n{'─' * 40}\n{_truncate(content, 2000)}\n{'─' * 40}"

  if tool_name == "edit_file":
    path = args.get("path", "?")
    old_text = args.get("old_text", "")
    new_text = args.get("new_text", "")
    return (
      f"📄 文件: {path}\n{'─' * 40}\n"
      f"删除内容:\n{'─' * 20}\n{_truncate(old_text, 1000)}\n{'─' * 20}\n"
      f"替换为:\n{'─' * 20}\n{_truncate(new_text, 1000)}\n{'─' * 20}"
    )

  if tool_name == "bash":
    command = args.get("command", "")
    return f"💻 命令:\n{'─' * 40}\n{command}\n{'─' * 40}"

  if tool_name == "git":
    command = args.get("command", "")
    return f"🔀 Git 命令:\n{'─' * 40}\ngit {command}\n{'─' * 40}"

  return json.dumps(args, ensure_ascii=False, indent=2)


# ──────────────────────────────────────────────
# 风险 key 提取（用于更细粒度的权限控制）
# ──────────────────────────────────────────────

def get_risk_key(tool_name: str, args: dict) -> str:
  """
  提取风险 key，用于会话级权限的细粒度匹配。
  
  例如 git commit 和 git push 是不同的 risk_key，
  用户可以单独授权 "本次会话允许所有 git commit"。
  """
  if tool_name == "git":
    return _extract_git_subcommand(args.get("command", ""))
  return "*"


# ──────────────────────────────────────────────
# 交互式权限确认
# ──────────────────────────────────────────────

YELLOW = "\033[33m"
RED    = "\033[31m"
GREEN  = "\033[32m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"


def _pick(options: list[tuple[str, str]]) -> Optional[str]:
  """
  方向键交互选择器。
  options: [(key, label), ...]，返回选中的 key，ESC/Ctrl-C 返回 None。
  """
  cursor = [0]
  chosen = [None]

  def get_text():
    parts: list[tuple[str, str]] = [("", "  ")]
    for i, (key, label) in enumerate(options):
      if i > 0:
        parts.append(("", "   "))
      if i == cursor[0]:
        parts.append(("reverse bold", f" [{key}] {label} "))
      else:
        parts.append(("", f" [{key}] {label} "))
    parts.append(("", "\n  "))
    parts.append(("italic", "← → 选择   Enter 确认   Esc 拒绝"))
    return parts

  kb = KeyBindings()

  @kb.add("left")
  def _(event): cursor[0] = (cursor[0] - 1) % len(options)

  @kb.add("right")
  def _(event): cursor[0] = (cursor[0] + 1) % len(options)

  @kb.add("enter")
  def _(event):
    chosen[0] = options[cursor[0]][0]
    event.app.exit()

  @kb.add("escape")
  @kb.add("c-c")
  def _(event): event.app.exit()

  # 同时保留直接按键快捷方式
  for key, _ in options:
    @kb.add(key)
    def handler(event, k=key):
      chosen[0] = k
      event.app.exit()

  layout = Layout(Window(FormattedTextControl(get_text, focusable=True)))
  Application(layout=layout, key_bindings=kb, full_screen=False).run()
  return chosen[0]


def confirm_permission(tool_name: str, args: dict, store: PermissionStore) -> bool:
  """
  交互式权限确认。
  
  Returns:
    True 表示允许执行，False 表示拒绝
  """
  risk_key = get_risk_key(tool_name, args)

  # 1. 检查会话级授权
  if store.is_allowed(tool_name, risk_key):
    return True

  # 2. 交互确认
  summary = build_summary(tool_name, args)

  while True:
    print(f"\n{YELLOW}{BOLD}⚠️  权限请求: {tool_name}{RESET}")
    for line in summary.splitlines():
      print(f"   {line}")
    print()

    options = [("y", "允许一次"), ("a", "始终允许"), ("n", "拒绝"), ("v", "查看详情")]
    choice = _pick(options)

    if choice is None:
      print(f"\n  {RED}✗ 已中断{RESET}")
      raise KeyboardInterrupt
    if choice == "n":
      print(f"\n  {RED}✗ 已拒绝{RESET}")
      return False
    elif choice == "y":
      print()
      return True
    elif choice == "a":
      store.grant(tool_name, risk_key)
      print(f"\n  {GREEN}✓ 已授权: 本次会话中 {tool_name}"
            f"{f' ({risk_key})' if risk_key != '*' else ''} 始终允许{RESET}")
      return True
    elif choice == "v":
      detail = build_detail(tool_name, args)
      print(f"\n{DIM}{detail}{RESET}")
      continue

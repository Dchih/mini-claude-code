"""
待办任务管理模块

提供多步骤任务的跟踪与展示：
- TodoItem: 单个待办项，包含描述、状态、子任务
- TodoStore: 待办列表管理器，支持增删改查和状态流转
- 终端友好展示：彩色状态图标、进度条、缩进子任务
"""

import json
import threading
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional


# ──────────────────────────────────────────────
# 状态定义
# ──────────────────────────────────────────────

class TodoStatus(str, Enum):
  PENDING     = "pending"
  IN_PROGRESS = "in_progress"
  COMPLETED   = "completed"


# 终端颜色
RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
RED    = "\033[31m"
CYAN   = "\033[36m"
WHITE  = "\033[37m"


# 状态 → 图标 + 颜色
STATUS_STYLE = {
  TodoStatus.PENDING:     ("○", DIM),
  TodoStatus.IN_PROGRESS: ("●", YELLOW + BOLD),
  TodoStatus.COMPLETED:   ("✓", GREEN),
}


# ──────────────────────────────────────────────
# 数据结构
# ──────────────────────────────────────────────

@dataclass
class TodoItem:
  """单个待办项"""
  id: str
  content: str
  status: TodoStatus = TodoStatus.PENDING
  activeForm: Optional[str] = None  # 执行时的进行时描述，如 "正在创建文件"

  def to_dict(self) -> dict:
    d = {
      "id": self.id,
      "content": self.content,
      "status": self.status.value,
    }
    if self.activeForm is not None:
      d["activeForm"] = self.activeForm
    return d

  @classmethod
  def from_dict(cls, d: dict) -> "TodoItem":
    return cls(
      id=d["id"],
      content=d["content"],
      status=TodoStatus(d.get("status", "pending")),
      activeForm=d.get("activeForm"),
    )


class TodoStore:
  """
  线程安全的待办列表管理器。

  用法：
    store = TodoStore()
    store.add("1", "读取项目结构")
    store.add("2", "实现核心功能")
    store.update("1", status=TodoStatus.IN_PROGRESS)
    store.update("1", status=TodoStatus.COMPLETED)
  """

  def __init__(self):
    self._items: list[TodoItem] = []
    self._lock = threading.Lock()
    self._changed = True  # 标记是否有变更，用于决定是否需要重新展示

  # ── 增删改查 ──

  def add(self, id: str, content: str, activeForm: Optional[str] = None) -> str:
    """添加待办项，如果 id 已存在则更新"""
    with self._lock:
      # 检查 id 是否已存在
      for item in self._items:
        if item.id == id:
          item.content = content
          item.activeForm = activeForm
          self._changed = True
          return f"已更新待办 [{id}]: {content}"

      self._items.append(TodoItem(id=id, content=content, activeForm=activeForm))
      self._changed = True
      return f"已添加待办 [{id}]: {content}"

  def update(self, id: str, status: Optional[TodoStatus] = None,
             content: Optional[str] = None,
             activeForm: Optional[str] = None) -> str:
    """更新待办项"""
    with self._lock:
      for item in self._items:
        if item.id == id:
          if status is not None:
            item.status = status
          if content is not None:
            item.content = content
          if activeForm is not None:
            item.activeForm = activeForm
          self._changed = True
          return f"已更新待办 [{id}] → {item.status.value}: {item.content}"
      return f"错误: 未找到待办项 [{id}]"

  def delete(self, id: str) -> str:
    """删除待办项"""
    with self._lock:
      for i, item in enumerate(self._items):
        if item.id == id:
          self._items.pop(i)
          self._changed = True
          return f"已删除待办 [{id}]"
      return f"错误: 未找到待办项 [{id}]"

  def get(self, id: str) -> Optional[TodoItem]:
    """获取单个待办项"""
    with self._lock:
      for item in self._items:
        if item.id == id:
          return item
      return None

  def list_all(self) -> list[TodoItem]:
    """获取所有待办项"""
    with self._lock:
      return list(self._items)

  def clear(self) -> str:
    """清空所有待办项"""
    with self._lock:
      count = len(self._items)
      self._items.clear()
      self._changed = True
      return f"已清空 {count} 个待办项"

  # ── 状态查询 ──

  @property
  def is_empty(self) -> bool:
    with self._lock:
      return len(self._items) == 0

  @property
  def has_changed(self) -> bool:
    return self._changed

  def mark_displayed(self):
    """标记已展示，清除 changed 标记"""
    self._changed = False

  @property
  def progress(self) -> tuple[int, int]:
    """返回 (已完成数, 总数)"""
    with self._lock:
      total = len(self._items)
      done = sum(1 for item in self._items if item.status == TodoStatus.COMPLETED)
      return (done, total)

  # ── 展示 ──

  def render(self, compact: bool = False) -> str:
    """
    渲染待办列表为终端友好的字符串。

    Args:
      compact: 紧凑模式，不显示进度条
    """
    with self._lock:
      if not self._items:
        return ""

      # 在锁内直接计算进度，避免调用 self.progress 导致死锁
      total = len(self._items)
      done = sum(1 for item in self._items if item.status == TodoStatus.COMPLETED)

      lines = []

      # 标题行
      if total > 0:
        pct = done * 100 // total
        bar_filled = "█" * done
        bar_empty = "░" * (total - done)
        lines.append(f"{BOLD}📋 待办进度{RESET}  {GREEN}{bar_filled}{DIM}{bar_empty}{RESET} {pct}% ({done}/{total})")
        lines.append("")

      for item in self._items:
        icon, color = STATUS_STYLE[item.status]

        # 状态图标 + 内容
        if item.status == TodoStatus.IN_PROGRESS and item.activeForm:
          display = f"  {color}{icon}{RESET} {item.id}. {item.activeForm}"
        else:
          # 已完成的用删除线效果（DIM）
          if item.status == TodoStatus.COMPLETED:
            display = f"  {color}{icon}{RESET} {DIM}{item.id}. {item.content}{RESET}"
          else:
            display = f"  {color}{icon}{RESET} {item.id}. {item.content}"

        lines.append(display)

      return "\n".join(lines)

  def to_json(self) -> str:
    """序列化为 JSON"""
    with self._lock:
      return json.dumps([item.to_dict() for item in self._items], ensure_ascii=False, indent=2)

  @classmethod
  def from_json(cls, json_str: str) -> "TodoStore":
    """从 JSON 反序列化"""
    store = cls()
    items = json.loads(json_str)
    for d in items:
      store._items.append(TodoItem.from_dict(d))
    return store


# ──────────────────────────────────────────────
# 工具函数：供 tool_use.py 调用
# ──────────────────────────────────────────────

def run_todo(action: str, id: str = "", content: str = "",
             status: str = "", activeForm: str = "") -> str:
  """
  待办任务管理工具。

  Actions:
    add      - 添加待办项（需要 id, content, 可选 activeForm）
    update   - 更新待办项（需要 id, 可选 content/status/activeForm）
    delete   - 删除待办项（需要 id）
    list     - 列出所有待办项
    clear    - 清空所有待办项
  """
  # 全局 store 实例（在 agent.py 初始化时注入）
  global _global_store

  if action == "add":
    if not id or not content:
      return "错误: add 操作需要 id 和 content 参数"
    return _global_store.add(id, content, activeForm or None)

  elif action == "update":
    if not id:
      return "错误: update 操作需要 id 参数"
    status_enum = None
    if status:
      try:
        status_enum = TodoStatus(status)
      except ValueError:
        return f"错误: 无效的状态值 '{status}'，可选: pending, in_progress, completed"
    return _global_store.update(id, status=status_enum, content=content or None,
                                activeForm=activeForm or None)

  elif action == "delete":
    if not id:
      return "错误: delete 操作需要 id 参数"
    return _global_store.delete(id)

  elif action == "list":
    items = _global_store.list_all()
    if not items:
      return "当前没有待办项"
    return _global_store.to_json()

  elif action == "clear":
    return _global_store.clear()

  else:
    return f"错误: 未知操作 '{action}'，可选: add, update, delete, list, clear"


# 全局 store 引用，由 agent.py 设置
_global_store: Optional[TodoStore] = None


def set_global_store(store: TodoStore):
  """设置全局 TodoStore 实例"""
  global _global_store
  _global_store = store

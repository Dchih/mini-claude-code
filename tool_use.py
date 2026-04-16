from pathlib import Path
from datetime import datetime
import subprocess
from todo import run_todo, set_global_store, TodoStore

WORKDIR = Path.cwd()
_PROJECT_FILE  = Path.home() / ".mini-claude-code" / "project"  / f"{WORKDIR.name}.md"
_SESSION_DIR   = Path.home() / ".mini-claude-code" / "sessions" / WORKDIR.name
_SESSION_FILE  = _SESSION_DIR / "current.md"
_SESSION_KEEP  = 10   # 最多保留多少条历史 session

def safe_path(p: str) -> Path:
  path = (WORKDIR / p).resolve()
  if not path.is_relative_to(WORKDIR):
    raise ValueError(f"Path escapes workspace: {p}")
  return path

def run_bash(command: str, timeout: int = 120) -> str:
  try:
    result = subprocess.run(
      command,
      shell=True,
      capture_output=True,
      text=True,
      timeout=timeout
    )
    return result.stdout or result.stderr
  except subprocess.TimeoutExpired:
    return f"error: command timed out after {timeout}s"

def run_read(path: str, limit: int = None) -> str:
  try:
    p = safe_path(path)
    text = p.read_text(encoding="utf-8")
    lines = text.splitlines()
    if limit is not None and limit < len(lines):
      lines = lines[:limit]
    return "\n".join(lines)[:50000]
  except FileNotFoundError:
    return f"error: file not found: {path}"
  except UnicodeDecodeError as e:
    return f"error: failed to decode file {path}: {e}"

def run_write(path: str, content: str) -> str:
  p = safe_path(path)
  p.parent.mkdir(parents=True, exist_ok=True)
  p.write_text(content, encoding="utf-8")
  return "ok"

def run_edit(path: str, old_text: str, new_text: str) -> str:
  try:
    p = safe_path(path)
    content = p.read_text(encoding="utf-8")
    if old_text not in content:
      return f"error: old_text not found in {path}"
    p.write_text(content.replace(old_text, new_text, 1), encoding="utf-8")
    return "ok"
  except FileNotFoundError:
    return f"error: file not found: {path}"
  except UnicodeDecodeError as e:
    return f"error: failed to decode file {path}: {e}"

def run_project_memory(action: str, scope: str = "project", content: str = "") -> str:
  """
  读写项目知识库。
  scope=project : 长期项目知识，跨会话有效
  scope=session : 当前会话进度，每次会话独立可 resume
  """
  if scope == "project":
    path = _PROJECT_FILE
  elif scope == "session":
    path = _SESSION_FILE
  else:
    return f"error: unknown scope '{scope}'，只支持 project / session"

  if action == "load":
    if not path.exists():
      return "no_summary"
    return path.read_text(encoding="utf-8")

  elif action == "save":
    path.parent.mkdir(parents=True, exist_ok=True)
    # session 存档：覆盖前先归档旧文件
    if scope == "session" and path.exists():
      ts = datetime.now().strftime("%Y%m%d_%H%M%S")
      path.rename(_SESSION_DIR / f"{ts}.md")
      # 只保留最近 N 条归档
      archives = sorted(
        (p for p in _SESSION_DIR.glob("????????_??????.md")),
        key=lambda p: p.name,
      )
      for old in archives[:-_SESSION_KEEP]:
        old.unlink()
    path.write_text(content, encoding="utf-8")
    return f"saved ({scope}): {path}"

  else:
    return f"error: unknown action '{action}'，只支持 load / save"


def run_git(command: str) -> str:
  """在工作目录下执行 git 命令，自动指定 -C 和 --no-pager"""
  full_cmd = f"git -C {WORKDIR} --no-pager {command}"
  return run_bash(full_cmd)

TOOL_HANDLES = {
  "bash": lambda **kw: run_bash(kw["command"], kw.get("timeout", 120)),
  "read_file": lambda **kw: run_read(kw["path"], kw.get("limit")),
  "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
  "edit_file": lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
  "git": lambda **kw: run_git(kw["command"]),
  "project_memory": lambda **kw: run_project_memory(kw["action"], kw.get("content", "")),
  "todo": lambda **kw: run_todo(
    kw["action"],
    id=kw.get("id", ""),
    content=kw.get("content", ""),
    status=kw.get("status", ""),
    activeForm=kw.get("activeForm", ""),
  ),
}

TOOL_DEFINITIONS = [
  {
    "type": "function",
    "function": {
      "name": "bash",
      "description": "执行 bash 命令，返回 stdout 或 stderr",
      "parameters": {
        "type": "object",
        "properties": {
          "command": {"type": "string", "description": "要执行的 bash 命令"},
          "timeout": {"type": "integer", "description": "命令超时时间（秒），默认 120"}
        },
        "required": ["command"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "read_file",
      "description": "读取文件内容",
      "parameters": {
        "type": "object",
        "properties": {
          "path": {"type": "string", "description": "相对于工作目录的文件路径"},
          "limit": {"type": "integer", "description": "最多读取的行数，不传则读取全部"}
        },
        "required": ["path"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "write_file",
      "description": "写入文件内容，文件不存在时自动创建",
      "parameters": {
        "type": "object",
        "properties": {
          "path": {"type": "string", "description": "相对于工作目录的文件路径"},
          "content": {"type": "string", "description": "要写入的文件内容"}
        },
        "required": ["path", "content"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "edit_file",
      "description": "替换文件中的指定文本",
      "parameters": {
        "type": "object",
        "properties": {
          "path": {"type": "string", "description": "相对于工作目录的文件路径"},
          "old_text": {"type": "string", "description": "要被替换的原始文本"},
          "new_text": {"type": "string", "description": "替换后的新文本"}
        },
        "required": ["path", "old_text", "new_text"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "git",
      "description": "在工作目录下执行 git 命令，自动指定仓库路径和 --no-pager",
      "parameters": {
        "type": "object",
        "properties": {
          "command": {"type": "string", "description": "git 子命令及参数，如 'log --oneline -10'、'diff'、'status'"}
        },
        "required": ["command"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "project_memory",
      "description": (
        "读写两类记忆：\n"
        "  scope=project：项目长期知识（架构/模块/约定），跨会话有效。\n"
        "  scope=session：当前会话进度（任务/决定/下一步），支持 resume。\n"
        "会话开始时先 load 两者；修改项目结构后 save project；"
        "完成重要步骤后 save session。"
      ),
      "parameters": {
        "type": "object",
        "properties": {
          "action": {
            "type": "string",
            "enum": ["load", "save"],
            "description": "load-读取；save-写入（session 会自动归档旧文件）"
          },
          "scope": {
            "type": "string",
            "enum": ["project", "session"],
            "description": (
              "project: 项目知识（必含 ## 项目概览 ## 模块结构 ## 关键文件 ## 设计约定 ## 依赖与外部接口）；\n"
              "session: 会话进度（必含 ## 当前任务 ## 已完成步骤 ## 关键决定 ## 下一步）"
            )
          },
          "content": {
            "type": "string",
            "description": "save 时必填，Markdown 格式"
          }
        },
        "required": ["action", "scope"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "todo",
      "description": "管理待办任务列表，用于规划和跟踪多步骤任务。开始复杂任务前应先创建待办列表，逐步推进并更新状态。",
      "parameters": {
        "type": "object",
        "properties": {
          "action": {
            "type": "string",
            "enum": ["add", "update", "delete", "list", "clear"],
            "description": "操作类型：add-添加待办, update-更新状态/内容, delete-删除, list-列出所有, clear-清空"
          },
          "id": {
            "type": "string",
            "description": "待办项 ID，如 '1', '2', '3'。add/update/delete 时必填"
          },
          "content": {
            "type": "string",
            "description": "待办项描述。add 时必填，update 时可选"
          },
          "status": {
            "type": "string",
            "enum": ["pending", "in_progress", "completed"],
            "description": "待办状态：pending-待处理, in_progress-进行中, completed-已完成。update 时可选"
          },
          "activeForm": {
            "type": "string",
            "description": "进行时描述，如 '正在创建文件'、'正在运行测试'。进行中状态显示此文本替代 content"
          }
        },
        "required": ["action"]
      }
    }
  }
]

from pathlib import Path
import subprocess

WORKDIR = Path.cwd()

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

def run_git(command: str) -> str:
  """在工作目录下执行 git 命令，自动指定 -C 和 --no-pager"""
  full_cmd = f"git -C {WORKDIR} --no-pager {command}"
  return run_bash(full_cmd)

TOOL_HANDLES = {
  "bash": lambda **kw: run_bash(kw["command"], kw.get("timeout", 120)),
  "read_file": lambda **kw: run_read(kw["path"], kw.get("limit")),
  "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
  "edit_file": lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
  "git": lambda **kw: run_git(kw["command"])
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
  }
]

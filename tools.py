import os
import subprocess
from pathlib import Path
from todo import TODO

WORKDIR = Path.cwd()

TOOLS = [
  {
    "name" : "bash",
    "description" : "Run a shell command and return its output.",
    "input_schema" : {
      "type" : "object",
      "properties" : {
        "command" : {
          "type" : "string",
          "description" : "The shell command to run."
        }
      },
      "required" : ["command"]
    }
  },
  {
    "name": "read_file",
    "description": "Read file contents, Use limit to read only first N lines.",
    "input_schema": {
      "type": "object",
      "properties": {
        "path": { "type": "string" },
        "limit": {"type": "integer", "description": "Max lines to read"},
      },
      "required": ["path"]
    }
  },
  {
      "name": "write_file",
      "description": "Write content to a file (creates parent dirs).",
      "input_schema": {
          "type": "object",
          "properties": {
              "path": {"type": "string"},
              "content": {"type": "string"},
          },
          "required": ["path", "content"],
      },
  },
  {
      "name": "edit_file",
      "description": "Replace exact text in file (first occurrence only).",
      "input_schema": {
          "type": "object",
          "properties": {
              "path": {"type": "string"},
              "old_text": {"type": "string", "description": "Text to find"},
              "new_text": {"type": "string", "description": "Replacement text"},
          },
          "required": ["path", "old_text", "new_text"],
      },
  },
  {
      "name": "subagent",
      "description": "Spawn a sub-agent to handle a complex subtask independently. It has access to all tools except subagent itself. Use this for parallel-style subtasks or isolated work.",
      "input_schema": {
          "type": "object",
          "properties": {
              "task": {
                  "type": "string",
                  "description": "A clear, self-contained task description for the sub-agent",
              },
          },
          "required": ["task"],
      },
  },
  {
      "name": "todo",
      "description": "Track your task progress. Send the FULL list each time (create/update/delete by inclusion).",
      "input_schema": {
          "type": "object",
          "properties": {
              "items": {
                  "type": "array",
                  "items": {
                      "type": "object",
                      "properties": {
                          "id": {"type": "string"},
                          "text": {"type": "string"},
                          "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                      },
                      "required": ["id", "text", "status"],
                  },
              },
          },
          "required": ["items"],
      },
  },
]

def safe_path(p: str) -> Path:
    path = (WORKDIR / p).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {p}")
    return path

def run_read(path: str, limit: int = None) -> str:
    try:
        lines = safe_path(path).read_text(encoding="utf-8").splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"...({len(lines) - limit} more lines)"]
        return "\n".join(lines)[:50000]
    except Exception as e:
        return f"Error: {e}"

def run_write(path: str, content: str) -> str:
    try:
        fp = safe_path(path)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"

def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        fp = safe_path(path)
        content = fp.read_text(encoding="utf-8")
        if old_text not in content:
            return f"Error: Text not found in {path}"
        fp.write_text(content.replace(old_text, new_text), encoding="utf-8")
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"

def run_bash(command: str) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot"]
    if any(d in command for d in dangerous):
        return "命令被禁止执行。"
    try:
        r = subprocess.run(
            ["bash", "-c", command],
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            timeout=120,
            encoding='utf-8',
            errors='replace'
        )
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        if not out and r.returncode != 0:
            return f"command failed with exit code {r.returncode}"
        return out[:50000] if out else "no output"
    except subprocess.TimeoutExpired:
        return "timeout 120s"
    except Exception as e:
        return f"Error: {e}"

def _run_subagent(task: str) -> str:
    """Lazy import to break circular dependency: agent -> tools -> agent."""
    from subagent import spawn_subagent
    return spawn_subagent(task)

TOOL_HANDLERS = {
    "bash":       lambda **kw: run_bash(kw["command"]),
    "read_file":  lambda **kw: run_read(kw["path"], kw.get("limit")),
    "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
    "edit_file":  lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
    "todo":       lambda **kw: TODO.update(kw["items"]),
    "subagent":   lambda **kw: _run_subagent(kw["task"])
}

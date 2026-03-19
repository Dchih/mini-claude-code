import os
import subprocess
from pathlib import Path

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
]

def safe_path(p: str) -> Path:
    path = (WORKDIR / p).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {p}")
    return path

def run_read(path: str, limit: int = None) -> str:
    try:
        lines = safe_path(path).read_text().splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"...({len(lines) - limit} more lines)"]
        return "\n".join(lines)[:50000]
    except Exception as e:
        return f"Error: {e}"

def run_write(path: str, content: str) -> str:
    try:
        fp = safe_path(path)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content)
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"

def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        fp = safe_path(path)
        content = fp.read_text()
        if old_text not in content:
            return f"Error: Text not found in {path}"
        fp.write_text(content.replace(old_text, new_text))
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"

def run_bash(command: str) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot"]
    if any(d in command for d in dangerous):
        return "命令被禁止执行。"
    try:
        if "\n" in command:
            r = subprocess.run(
                ["bash", "-c", command],
                cwd=os.getcwd(),
                capture_output=True,
                text=True,
                timeout=120,
                encoding='utf-8',
                errors='replace'
            )
        else:
            r = subprocess.run(
                command,
                shell=True,
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

TOOL_HANDLERS = {
    "bash":       lambda **kw: run_bash(kw["command"]),
    "read_file":  lambda **kw: run_read(kw["path"], kw.get("limit")),
    "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
    "edit_file":  lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
}

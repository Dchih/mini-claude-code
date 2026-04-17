"""
Skill 系统 —— 逐步披露（progressive disclosure）

目录结构：
  ~/.mini-claude-code/skills/<skill-name>/SKILL.md

SKILL.md 格式（YAML frontmatter + Markdown 正文）：
  ---
  name: code-review
  description: 逐文件审查代码，输出结构化审查报告
  ---

  ## 使用场景
  ...

流程：
  1. 启动时扫描 skills/，解析 frontmatter，构建轻量目录
  2. 目录注入 system prompt（只含名称 + 一句话描述）
  3. 模型调用 load_skill(name) → 返回完整 SKILL.md 正文
"""

from pathlib import Path
import re
from typing import Optional, List

# ── 配置 ──────────────────────────────────────────
SKILLS_DIR = Path.home() / ".mini-claude-code" / "skills"

# frontmatter 解析正则
_FM_RE = re.compile(
    r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL
)


# ── 数据结构 ──────────────────────────────────────

class SkillMeta:
    """一个 skill 的元信息（从 frontmatter 解析）"""
    __slots__ = ("name", "description", "path")

    def __init__(self, name: str, description: str, path: Path):
        self.name = name
        self.description = description
        self.path = path


# ── 解析 ──────────────────────────────────────────

def _parse_frontmatter(text: str) -> dict:
    """解析 YAML frontmatter，返回简单 dict。不引入 pyyaml，手写极简解析。"""
    m = _FM_RE.match(text)
    if not m:
        return {}
    result = {}
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, _, val = line.partition(":")
            result[key.strip()] = val.strip().strip('"').strip("'")
    return result


def _load_skill_meta(skill_dir: Path) -> Optional[SkillMeta]:
    """从单个 skill 目录加载元信息，失败返回 None。"""
    md_path = skill_dir / "SKILL.md"
    if not md_path.is_file():
        return None
    try:
        text = md_path.read_text(encoding="utf-8")
    except OSError:
        return None
    fm = _parse_frontmatter(text)
    name = fm.get("name") or skill_dir.name
    description = fm.get("description", "")
    return SkillMeta(name=name, description=description, path=md_path)


# ── 目录构建 ──────────────────────────────────────

def scan_skills() -> List[SkillMeta]:
    """扫描 SKILLS_DIR，返回所有合法 skill 的元信息列表。"""
    if not SKILLS_DIR.is_dir():
        return []
    skills = []
    for child in sorted(SKILLS_DIR.iterdir()):
        if child.is_dir():
            meta = _load_skill_meta(child)
            if meta is not None:
                skills.append(meta)
    return skills


def build_catalog_text(skills: List[SkillMeta]) -> str:
    """生成注入 system prompt 的轻量目录文本。"""
    if not skills:
        return ""
    lines = ["## 可用技能（Skills）", ""]
    lines.append("需要时调用 load_skill(name) 加载完整技能内容。")
    lines.append("")
    for s in skills:
        desc = f"：{s.description}" if s.description else ""
        lines.append(f"- **{s.name}**{desc}")
    lines.append("")
    return "\n".join(lines)


# ── 加载完整 skill ────────────────────────────────

def load_skill(name: str, skills: Optional[List[SkillMeta]] = None) -> str:
    """
    按名称加载完整 SKILL.md 正文。
    如果 skills 未提供则重新扫描。
    找不到时返回错误提示。
    """
    if skills is None:
        skills = scan_skills()
    for s in skills:
        if s.name == name:
            try:
                return s.path.read_text(encoding="utf-8")
            except OSError as e:
                return f"error: failed to read skill '{name}': {e}"
    available = ", ".join(s.name for s in skills) or "(无)"
    return f"error: skill '{name}' not found. available: {available}"


# ── 模块级缓存 ────────────────────────────────────

_cached_skills: Optional[List[SkillMeta]] = None


def get_skills() -> List[SkillMeta]:
    """获取（缓存的）skill 列表。"""
    global _cached_skills
    if _cached_skills is None:
        _cached_skills = scan_skills()
    return _cached_skills


def refresh_skills() -> List[SkillMeta]:
    """强制重新扫描 skill 目录。"""
    global _cached_skills
    _cached_skills = scan_skills()
    return _cached_skills

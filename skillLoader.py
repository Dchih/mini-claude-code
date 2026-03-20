from pathlib import Path
import re

SKILL_DIR = Path.cwd() / "skills"

class SkillLoader:
  def __init__(self, skill_dir: Path):
    self.skill_dir = skill_dir
    self.skills = {}
    self._scan()

  def _scan(self):
    """start scan all skills: skills/Skill.md"""

    if not self.skill_dir.exists():
      return
    for f in sorted(self.skill_dir.rglob("SKILL.md")):
      text = f.read_text(encoding="utf-8")
      meta, body = self._parse_frontmatter(text)
      name = meta.get("name", f.parent.name)
      self.skills[name] = {"meta": meta, "body": body}
      print(f"  [skill loaded] {name}: {meta.get('description', '(no desc)')}")

  def _parse_frontmatter(self, text: str) -> tuple:
    """
    解析 YMAL frontmatter：
    ---
    name: code-review
    description: Review code quality
    ---
    正文内容...
    """
    match = re.match(r"^---\n(.*?)\n---\n(.*)", text, re.DOTALL)
    if not match:
      return {}, text
    
    meta = {}
    for line in match.group(1).strip().splitlines():
      if ":" in line:
        key, val = line.split(":")
        meta[key.strip()] = val.strip()
      
    return meta, match.group(2).strip()
  
  def get_description(self) -> str:
    """Layer 1: return short desc, append to system propmt"""

    if not self.skills:
      return "(no skills available)"
    lines = []
    for name, skill in self.skills.items():
      desc = skill["meta"].get("description", "No description")
      lines.append(f" - {name}: {desc}")
    return "\n".join(lines)
  
  def get_content(self, name: str) -> str:
    """Layer 2: return full content, inject as tool_result"""
    skill = self.skills.get(name)
    if not skill:
      available = ", ".join(self.skills.keys())
      return f"Error: Unknown skill '{name}'. Available: {available}"
    return f"<skill name=\"{name}\">\n{skill['body']}\n</skill>"
  

SKILL_LOADER = SkillLoader(SKILL_DIR)
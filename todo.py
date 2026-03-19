class TodoManager:
  """Tool for the model to track its own task progress."""

  def __init__(self):
    self.items = []

  def update(self, items: list) -> str:
    """update task list, validated"""
    if len(items) > 20:
      raise ValueError(f"Max 20 todos allowed")
    
    validated = []
    in_progress_count = 0

    for item in items:
      text = str(item.get("text", "")).strip()
      status = str(item.get("status", "pending")).lower()
      item_id = str(item.get("id", str(len(validated) + 1 )))

      if not text:
          raise ValueError(f"Item {item_id}: text required")
      if status not in ("pending", "in_progress", "completed"):
          raise ValueError(f"Invalid status: {status}")
      if status == "in_progress":
          in_progress_count += 1
      
      validated.append({"id": item_id, "text": text, "status": status})

    if in_progress_count > 1:
       raise ValueError(f"Only one task can be in_progress at a time")
    
    self.items = validated
    return self.render()
  
  def render(self) -> str:
    if not self.items:
      return "NO TODOS"
     
    lines = []
    for item in self.items:
       marker = {
          "pending": "[ ]",
          "in_progress": "[>]",
          "completed": "[x]"
       }[item["status"]]
       lines.append(f"{marker} #{item['id']}: {item['text']}")
    done = sum(1 for t in self.items if t["status"] == "completed")
    lines.append(f"\n({done}/{len(self.items)} completed)")
    return "\n".join(lines)
  
TODO = TodoManager()

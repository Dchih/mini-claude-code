---
name: code-review
description: Review code for bugs, security issues, and style problems
---

## Code Review Checklist

### Bugs
- Check for off-by-one errors in loops
- Look for unhandled null/undefined values
- Verify async operations have proper error handling
- Check for race conditions in concurrent code

### Security
- SQL injection: are queries parameterized?
- XSS: is user input sanitized before rendering?
- Secrets: are API keys hardcoded?
- Dependencies: any known vulnerabilities?

### Style
- Functions should do one thing
- Names should be descriptive (no single-letter variables except loop counters)
- Max function length: 30 lines
- DRY: is there duplicated logic that should be extracted?

### Output Format
For each issue found, report:
1. File and line number
2. Severity: critical / warning / suggestion
3. What the problem is
4. How to fix it

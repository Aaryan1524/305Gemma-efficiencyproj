"""Check local Markdown links without network access."""
import re
from pathlib import Path

root = Path(__file__).resolve().parents[1]
errors = []
files = list(root.glob("*.md")) + list((root / "docs").rglob("*.md"))
# Untracked personal writeups are outside the maintained documentation surface.
files = [p for p in files if p.name not in {"DEVPOST.md", "writeup.md", "focusledger-build-guide.md"}]
for path in files:
    for target in re.findall(r'\]\(([^)]+)\)', path.read_text()):
        if ":" in target or target.startswith("#"):
            continue
        target = target.split("#")[0]
        if not (path.parent / target).exists():
            errors.append(f"{path.relative_to(root)}: {target}")
if errors:
    raise SystemExit("Broken local links:\n" + "\n".join(errors))
print(f"Local links checked in {len(files)} documents")

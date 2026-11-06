"""Shared per-user storage. Importing this module never creates files."""
import os
from pathlib import Path
import shutil

BASE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("FL_DATA_DIR", Path.home() / "Library/Application Support/FocusLedger")).expanduser().resolve()
LEDGER_PATH = str(Path(os.environ.get("FL_LEDGER_PATH", DATA_DIR / "ledger.jsonl")).expanduser().resolve())
REPORT_PATH = str(DATA_DIR / "report.html")


def initialize():
    """Copy legacy history once, never overwrite or remove either history."""
    DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.environ.get("FL_LEDGER_PATH"):
        return
    source = BASE / "ledger.jsonl"
    target = Path(LEDGER_PATH)
    if source.exists() and not target.exists():
        try:
            with target.open("xb") as dest, source.open("rb") as src:
                shutil.copyfileobj(src, dest)
        except FileExistsError:
            return
        except OSError:
            target.unlink(missing_ok=True)
            raise RuntimeError("History migration failed; original history is preserved.") from None


def rows_for_day(rows, day=None):
    """Legacy undated rows inherit their preceding goals date."""
    dated = []
    current = None
    for row in rows:
        current = row.get("date", current)
        dated.append((current, row))
    selected = day or next((d for d, _ in reversed(dated) if d), None)
    return [r for d, r in dated if d == selected]

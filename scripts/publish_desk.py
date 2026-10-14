"""Write the public paper-book page data. No keys or sheet ids."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.desk import build_desk


def main() -> None:
    payload = json.dumps(build_desk(ROOT), indent=2) + "\n"
    out = ROOT / "site" / "data" / "desk.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(payload, encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()

"""Embed a screen of the FICTIONAL sample universe in app/sharepicker.html as its demo.

    python scripts/build_sharepicker_demo.py
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sharepicker.cli import add_briefs_json, add_research_json, research_context  # noqa: E402
from sharepicker.research import save_history  # noqa: E402
from sharepicker.data import CsvSource  # noqa: E402
from sharepicker.report import dumps, screen_json  # noqa: E402
from sharepicker.sample import generate  # noqa: E402
from sharepicker.screen import run_screen  # noqa: E402

AS_OF = date(2026, 11, 20)


def _round(o):
    if isinstance(o, float):
        return round(o, 5)
    if isinstance(o, dict):
        return {k: _round(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_round(v) for v in o]
    return o


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        folder = generate(tmp)
        cfg = {"data": {"folder": str(folder)}}
        src = CsvSource(folder)
        res = run_screen(cfg, src, AS_OF)
        data = screen_json(res)
        # Save a few earlier screens so the demo digest and score history have something to show.
        hist = Path(tmp) / "history"
        cfg["research"] = {"history_dir": str(hist)}
        for d in (date(2026, 8, 31), date(2026, 9, 30), date(2026, 10, 30)):
            save_history(run_screen(cfg, src, d), hist)
        ctx, hist_dir = research_context(cfg, src, AS_OF)
        add_research_json(data, res, ctx)
        add_briefs_json(data, res, ctx, hist_dir)
        data = _round(json.loads(dumps(data)))
    data["source"] = "fictional sample data"
    blob = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    html_path = ROOT / "app" / "sharepicker.html"
    html = html_path.read_text(encoding="utf-8")
    html, n = re.subn(r'(<script id="demo-data" type="application/json">)[\s\S]*?(</script>)',
                      lambda m: m.group(1) + blob + m.group(2), html)
    if n != 1:
        print("demo-data tag not found", file=sys.stderr)
        return 1
    html_path.write_text(html, encoding="utf-8")
    print(f"Embedded {len(data['shares'])} shares ({len(blob) // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Build the live NZX/ASX screen the Sharepicker page loads as `screen.json`.

    python scripts/refresh_live.py                       # -> my-data/publish/screen.json
    python scripts/refresh_live.py --previous old.json   # compare with the last published screen

`--previous` takes the screen.json that was published last time and adds it to the history,
so the "Changes" tab works even in a fresh session with no local history.
Needs network access to query1.finance.yahoo.com.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sharepicker.cli import add_briefs_json, add_research_json, load_config, research_context, resolve_as_of  # noqa: E402
from sharepicker.data import open_source  # noqa: E402
from sharepicker.feeds import Fetcher, collect  # noqa: E402
from sharepicker.report import dumps, screen_json  # noqa: E402
from sharepicker.research import save_history  # noqa: E402
from sharepicker.screen import run_screen  # noqa: E402


def history_from_published(path: Path, hist_dir: Path) -> str | None:
    """Turn a published screen.json back into a history entry (rank, score, red flags)."""
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not d.get("shares") or "fictional" in str(d.get("source", "")):
        return None
    entry = {"as_of": d["as_of"], "weights": d.get("weights", {}),
             "shares": {s["ticker"]: {"rank": s["rank"], "composite": s["composite"], "groups": s["groups"],
                                      "red": [f["text"] for f in s.get("flags", []) if f["kind"] == "red"]}
                        for s in d["shares"]}}
    hist_dir.mkdir(parents=True, exist_ok=True)
    (hist_dir / f"screen-{d['as_of']}.json").write_text(json.dumps(entry, indent=1), encoding="utf-8")
    return d["as_of"]


def _round(o):
    if isinstance(o, float):
        return round(o, 5)
    if isinstance(o, dict):
        return {k: _round(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_round(v) for v in o]
    return o


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "examples" / "live" / "screen.yaml"))
    ap.add_argument("--out", default="my-data/publish/screen.json")
    ap.add_argument("--previous", help="Last published screen.json, for the Changes tab")
    ap.add_argument("--previous-archive", help="Last published archive.json (announcements, news, short interest)")
    ap.add_argument("--no-feeds", action="store_true", help="Skip announcements, news and short interest")
    args = ap.parse_args()

    cfg = load_config(args.config)
    source = open_source(cfg)
    as_of = resolve_as_of(cfg, source)
    hist_dir = Path((cfg.get("research") or {}).get("history_dir") or "my-data/live-history")
    if args.previous:
        prev = history_from_published(Path(args.previous), hist_dir)
        print(f"Previous screen: {prev or 'none usable'}", file=sys.stderr)

    # Research feeds: merge with the previous archive so nothing collected is lost.
    arch_path = Path((cfg.get("research") or {}).get("archive") or "my-data/publish/archive.json")
    old = None
    if args.previous_archive and Path(args.previous_archive).exists():
        old = json.loads(Path(args.previous_archive).read_text(encoding="utf-8"))
    elif arch_path.exists():
        old = json.loads(arch_path.read_text(encoding="utf-8"))
    if not args.no_feeds:
        print("Collecting research feeds:", file=sys.stderr)
        fetcher = Fetcher(Path((cfg.get("data") or {}).get("cache_dir", "my-data/cache")) / "feeds")
        archive = collect(source.universe(), fetcher, old, log=lambda m: print(m, file=sys.stderr))
        before = sum(len((old or {}).get(k, [])) for k in ("announcements", "news", "shorts"))
        after = sum(len(archive[k]) for k in ("announcements", "news", "shorts"))
        if after < before:
            print(f"Archive would shrink ({before} -> {after} items): not writing it.", file=sys.stderr)
            return 2
        arch_path.parent.mkdir(parents=True, exist_ok=True)
        arch_path.write_text(json.dumps(archive, separators=(",", ":")), encoding="utf-8")
        print(f"Archive: {after} items ({after - before:+d})", file=sys.stderr)
        if hasattr(source, "load_imputation"):
            source.load_imputation(archive)     # NZX imputation credits collected just now
    elif old is not None:
        arch_path.parent.mkdir(parents=True, exist_ok=True)
        arch_path.write_text(json.dumps(old, separators=(",", ":")), encoding="utf-8")

    res = run_screen(cfg, source, as_of)
    ctx, hdir = research_context(cfg, source, as_of)
    data = screen_json(res)
    add_research_json(data, res, ctx)
    add_briefs_json(data, res, ctx, hdir, float(cfg.get("marginal_tax_rate", 0.33)))
    data["source"] = f"Yahoo Finance, fetched {date.today():%d %b %Y}"
    data["live"] = True
    errors = getattr(source, "errors", {})
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(_round(json.loads(dumps(data))), separators=(",", ":")), encoding="utf-8")
    save_history(res, hist_dir)
    print(f"Wrote {out}: {len(res.ranked)} shares as at {as_of}, {len(errors)} fetch errors", file=sys.stderr)
    return 0 if res.ranked else 1


if __name__ == "__main__":
    sys.exit(main())

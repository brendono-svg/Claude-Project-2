"""Command line.

    python -m sharepicker sample-data my-data/sample
    python -m sharepicker screen my-data/screen.yaml -o my-data/screen.md [--json my-data/screen.json]
    python -m sharepicker explain my-data/screen.yaml KAU.NZ
    python -m sharepicker backtest my-data/screen.yaml [-o my-data/backtest.md]
    python -m sharepicker watchlist my-data/holdings.yaml [-o my-data/watch.md]
    python -m sharepicker fetch my-data/screen.yaml FPH.NZ CBA.AX   # live Yahoo check
    python -m sharepicker brief my-data/screen.yaml KAU.NZ [--for-claude] [-o brief.md]
    python -m sharepicker digest my-data/screen.yaml [--save]
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import yaml

from . import market_rules as mr
from .backtest import run_backtest
from .data import YahooSource, open_source
from .report import dumps, render_backtest, render_screen, render_watch, screen_json, why
from .sample import generate
from .brief import Context, announcements_with_reactions, render_brief, render_digest, research_for
from .research import ResearchStore, load_history, save_history
from .screen import DEFAULT_BENCHMARKS, load_benchmarks, run_screen


def load_config(path: str | None) -> dict:
    if not path:
        return {}
    cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    base = Path(path).parent
    data = cfg.get("data") or {}
    if "folder" in data and not Path(data["folder"]).is_absolute() and not Path(data["folder"]).exists():
        # Let configs use paths relative to themselves.
        cand = base / data["folder"]
        if cand.exists():
            data["folder"] = str(cand)
    return cfg


def resolve_as_of(cfg: dict, source) -> date:
    if cfg.get("as_of"):
        v = cfg["as_of"]
        return v if isinstance(v, date) else date.fromisoformat(str(v))
    last = None
    for t in {**DEFAULT_BENCHMARKS, **(cfg.get("benchmarks") or {})}.values():
        s = source.prices(t)
        if len(s) and (last is None or s.last_date > last):
            last = s.last_date
    return min(date.today(), last) if last else date.today()


def _out(text: str, path: str | None) -> None:
    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(text, encoding="utf-8")
        print(f"Wrote {path}", file=sys.stderr)
    else:
        print(text)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="sharepicker", description="NZX / ASX share screener (not financial advice)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("sample-data", help="Write a FICTIONAL sample universe to try the tool")
    sp.add_argument("folder", nargs="?", default="my-data/sample")

    sc = sub.add_parser("screen", help="Rank the universe and explain the top picks")
    sc.add_argument("config", nargs="?")
    sc.add_argument("-o", "--output")
    sc.add_argument("--json", help="Also write the full results as JSON (for app/sharepicker.html)")
    sc.add_argument("--preset", help="Override weights: balanced, value, quality, income, momentum")
    sc.add_argument("--top", type=int)
    sc.add_argument("--as-of")
    sc.add_argument("--save", action="store_true", help="Save this screen to the history folder (the tool's memory)")
    sc.add_argument("--with-briefs", action="store_true",
                    help="Put every share's brief and the digest in the JSON, for the app's Briefs and Digest tabs")

    br = sub.add_parser("brief", help="Research file for one share: checklist, thesis, results, announcements, sources")
    br.add_argument("config")
    br.add_argument("ticker")
    br.add_argument("-o", "--output")
    br.add_argument("--for-claude", action="store_true", help="Add a prompt to paste into Claude with the documents")
    br.add_argument("--as-of")

    dg = sub.add_parser("digest", help="What changed since the last saved screen")
    dg.add_argument("config", nargs="?")
    dg.add_argument("-o", "--output")
    dg.add_argument("--save", action="store_true", help="Save today's screen to the history afterwards")
    dg.add_argument("--as-of")

    ex = sub.add_parser("explain", help="Everything the screen knows about one share")
    ex.add_argument("config")
    ex.add_argument("ticker")

    bt = sub.add_parser("backtest", help="Monthly top-N backtest")
    bt.add_argument("config", nargs="?")
    bt.add_argument("-o", "--output")
    bt.add_argument("--start")
    bt.add_argument("--end")

    wl = sub.add_parser("watchlist", help="Score your holdings, check concentration, FIF and satellite room")
    wl.add_argument("holdings")
    wl.add_argument("-o", "--output")

    fe = sub.add_parser("fetch", help="Fetch tickers live from Yahoo and report what came back")
    fe.add_argument("config", nargs="?")
    fe.add_argument("tickers", nargs="+")

    nt = sub.add_parser("note", help="Add a dated research note for a share")
    nt.add_argument("config")
    nt.add_argument("ticker")
    nt.add_argument("text", nargs="+")

    an = sub.add_parser("announce", help="Log an announcement you read on the NZX/ASX site")
    an.add_argument("config")
    an.add_argument("ticker")
    an.add_argument("date", help="YYYY-MM-DD")
    an.add_argument("headline")
    an.add_argument("--url", default="")
    an.add_argument("--sensitive", action="store_true", help="Marked price-sensitive")

    args = ap.parse_args(argv)

    if args.cmd in ("note", "announce"):
        return _capture(args)

    if args.cmd == "sample-data":
        p = generate(args.folder)
        print(f"Wrote fictional sample data to {p}. Try: python -m sharepicker screen examples/screen.example.yaml")
        return 0

    if args.cmd == "fetch":
        cfg = load_config(args.config)
        d = cfg.get("data") or {}
        y = YahooSource(d.get("cache_dir", "my-data/cache/yahoo"), cache_days=0)
        ok = True
        for t in args.tickers:
            s = y.prices(t)
            divs = y.dividends(t)
            if len(s):
                print(f"{t}: {len(s)} daily prices {s.dates[0]} to {s.last_date}, last close {s.values[-1]:.3f}, "
                      f"{len(divs)} dividends")
            else:
                ok = False
                print(f"{t}: FAILED {y.errors.get(t, 'no data')}")
        return 0 if ok else 1

    if args.cmd == "watchlist":
        from kiwiwealth.profile import load_profile

        from .portfolio import watch
        wcfg = yaml.safe_load(Path(args.holdings).read_text(encoding="utf-8")) or {}
        base = Path(args.holdings).parent
        rel = lambda p: str(p if Path(p).exists() or Path(p).is_absolute() else base / p)  # noqa: E731
        cfg = load_config(rel(wcfg["screen"])) if wcfg.get("screen") else {}
        source = open_source(cfg)
        as_of = resolve_as_of(cfg, source)
        profile = load_profile(rel(wcfg["profile"])) if wcfg.get("profile") else None
        if profile and "marginal_tax_rate" not in cfg and profile.people:
            from kiwiwealth import nz_rules as nz
            cfg["marginal_tax_rate"] = max(nz.marginal_tax_rate(p.taxable_income, as_of) for p in profile.people)
        res = run_screen(cfg, source, as_of)
        w = watch(wcfg.get("holdings") or [], res, source, as_of, profile,
                  max_sector_pct=float(wcfg.get("max_sector_pct", 0.35)), broker=wcfg.get("broker", "sharesies"),
                  include_profile_offshore=bool(wcfg.get("include_profile_offshore", True)))
        _out(render_watch(w, as_of), args.output)
        return 0

    cfg = load_config(args.config)
    source = open_source(cfg)

    if args.cmd == "backtest":
        b = cfg.get("backtest") or {}
        end = date.fromisoformat(args.end or str(b.get("end") or resolve_as_of(cfg, source)))
        start = date.fromisoformat(args.start or str(b.get("start") or date(end.year - 5, end.month, 1)))
        res = run_backtest(cfg, source, start, end)
        _out(render_backtest(res, cfg), args.output)
        return 0

    if getattr(args, "preset", None):
        cfg["weights"] = args.preset
    as_of = date.fromisoformat(args.as_of) if getattr(args, "as_of", None) else resolve_as_of(cfg, source)
    res = run_screen(cfg, source, as_of)

    if args.cmd == "explain":
        return _explain(res, args.ticker.upper(), float(cfg.get("marginal_tax_rate", 0.33)))

    if args.cmd in ("brief", "digest", "screen"):
        ctx, hist_dir = research_context(cfg, source, as_of)
    if args.cmd == "brief":
        md = render_brief(res, args.ticker, ctx, args.for_claude, float(cfg.get("marginal_tax_rate", 0.33)))
        if md is None:
            print(f"{args.ticker}: {res.excluded.get(args.ticker.upper(), 'not in the universe')}", file=sys.stderr)
            return 1
        _out(md, args.output)
        return 0
    if args.cmd == "digest":
        _out(render_digest(res, ctx, hist_dir), args.output)
        if args.save:
            print(f"Saved {save_history(res, hist_dir)}", file=sys.stderr)
        return 0

    top = args.top or int(cfg.get("top_n", 10))
    _out(render_screen(res, top, float(cfg.get("marginal_tax_rate", 0.33))), args.output)
    if args.json:
        data = screen_json(res)
        add_research_json(data, res, ctx)
        if args.with_briefs:
            add_briefs_json(data, res, ctx, hist_dir, float(cfg.get("marginal_tax_rate", 0.33)))
        _out(dumps(data), args.json)
    if args.save:
        print(f"Saved {save_history(res, hist_dir)}", file=sys.stderr)
    return 0


def _explain(res, ticker: str, rate: float) -> int:
    from .factors import METRICS
    r = next((x for x in res.ranked if x.ticker.upper() == ticker), None)
    if r is None:
        alias = {o: m for m, os in res.other_listings.items() for o in os}
        if ticker in alias:
            print(f"{ticker} is scored on its more liquid listing {alias[ticker]}.")
            return _explain(res, alias[ticker], rate)
        print(f"{ticker}: {res.excluded.get(ticker, 'not in the universe')}")
        return 1
    lst = r.snap.listing
    print(f"{lst.name} ({lst.ticker}, {lst.exchange}, {lst.sector}) on {res.as_of}")
    print(f"Rank {r.rank} of {len(res.ranked)}, score {r.composite:.1f}, confidence {r.confidence}")
    print(why(r))
    print()
    print(f"{'metric':<34}{'value':>10}{'percentile':>12}")
    for k, (g, _, label) in METRICS.items():
        v, sc = r.snap.metrics.get(k), r.metric_scores.get(k)
        vs = "–" if v is None else (f"{v:.2f}" if k in ("debt_to_equity", "interest_cover", "dividend_cover", "fcf_cover", "beta",
                                                          "near_52w_high", "earnings_consistency") else f"{v:.1%}")
        print(f"{g + ': ' + label:<34}{vs:>10}{'–' if sc is None else f'{sc:.0f}':>12}")
    print()
    t = res.fif.get(lst.ticker)
    if t:
        print(f"Tax: {t[1]}")
    f = r.snap.facts
    if f.get("dps_ttm"):
        d = mr.dividend_for_nz_resident(1.0, lst.domicile, imputed_pct=1.0 if lst.domicile == "NZ" else 0,
                                        franked_pct=1.0 if lst.domicile == "AU" else 0, marginal_rate=rate)
        print(f"Per $1 of fully imputed/franked cash dividend at a {rate:.0%} tax rate you keep ${d['net']:.3f} "
              f"(NZ credits ${d['nz_credits']:.3f}, withholding ${d['withholding']:.3f}).")
    for kind, text in res.flags.get(lst.ticker, []):
        print(f"{'RED  ' if kind == 'red' else 'GREEN'} {text}")
    return 0


def research_context(cfg: dict, source, as_of: date) -> tuple[Context, str]:
    d = cfg.get("data") or {}
    r = cfg.get("research") or {}
    folder = Path(d.get("folder", "my-data/sample"))
    hist_dir = str(r.get("history_dir") or folder / "history")
    store = ResearchStore(folder, r.get("journal"))
    arch = r.get("archive")
    if arch and Path(arch).exists():
        import json
        store.add_archive(json.loads(Path(arch).read_text(encoding="utf-8")))
    companies: dict[str, list[str]] = {}
    for lst in source.universe():
        companies.setdefault(lst.company_id, []).append(lst.ticker)
    store.aliases = {t: ts for ts in companies.values() for t in ts}
    websites = {}
    lp = folder / "listings.csv"
    if lp.exists():
        import csv
        with lp.open(newline="", encoding="utf-8-sig") as fh:
            websites = {row["ticker"]: row.get("website", "") for row in csv.DictReader(fh) if row.get("website")}
    bench = load_benchmarks(source, {**DEFAULT_BENCHMARKS, **(cfg.get("benchmarks") or {})})
    return Context(source, store, bench, load_history(hist_dir, before=as_of), websites), hist_dir


def add_research_json(data: dict, res, ctx: Context) -> None:
    """Checklist, next results and top announcements per share, for app/sharepicker.html."""
    by = {r.ticker: r for r in res.ranked}
    for sh in data["shares"]:
        r = by[sh["ticker"]]
        rs = research_for(r, res, ctx)
        nr = rs["next_results"]
        anns = announcements_with_reactions(ctx, r.ticker, r.snap.listing.exchange, res.as_of, days=180)
        top = sorted(anns, key=lambda a: -a.relevance)[:4]
        sh["research"] = {
            "lenses": rs["lenses"],
            "rules": rs["rules"],
            "thesis": rs["journal"].thesis if rs["journal"] else None,
            "next_results": {"date": nr["date"].isoformat(), "basis": nr["basis"]} if nr else None,
            "announcements": [{"date": a.date.isoformat(), "label": a.label, "headline": a.headline,
                               "url": a.url, "reaction": a.reaction} for a in sorted(top, key=lambda a: a.date, reverse=True)],
        }


def _capture(args) -> int:
    import csv
    cfg = load_config(args.config)
    folder = Path((cfg.get("data") or {}).get("folder", "my-data/sample"))
    t = args.ticker.upper()
    if args.cmd == "note":
        d = folder / "research" / t
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{date.today().isoformat()}-notes.md"
        new = not path.exists()
        with path.open("a", encoding="utf-8") as fh:
            if new:
                fh.write(f"# {t} notes, {date.today():%d %B %Y}\n\n")
            fh.write(f"- {' '.join(args.text)}\n")
        print(f"Added to {path}")
        return 0
    from .research import classify
    date.fromisoformat(args.date)   # validate
    path = folder / "announcements.csv"
    fields = ["date", "ticker", "headline", "url", "price_sensitive"]
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerow({"date": args.date, "ticker": t, "headline": args.headline, "url": args.url,
                    "price_sensitive": 1 if args.sensitive else ""})
    print(f"Logged in {path} as: {classify(args.headline)[0]}")
    return 0


def add_briefs_json(data: dict, res, ctx: Context, hist_dir: str, rate: float = 0.33) -> None:
    """Rendered briefs (plus the paste-ready Claude prompt) and the digest, as HTML for the app."""
    import re

    from .mdhtml import to_html
    from .report import DISCLAIMER

    def strip(md: str) -> str:   # the app shows the disclaimer once, in its footer
        return md.replace(DISCLAIMER, "")
    briefs = {}
    for r in res.ranked:
        md = render_brief(res, r.ticker, ctx, for_claude=True, marginal_rate=rate) or ""
        body, _, pack = md.partition("## Prompt to paste into Claude")
        m = re.search(r"```text\n(.*?)\n```", pack, re.S)
        briefs[r.ticker] = {"html": to_html(strip(body)), "prompt": m.group(1) if m else ""}
    data["briefs"] = briefs
    data["digest_html"] = to_html(strip(render_digest(res, ctx, hist_dir)))

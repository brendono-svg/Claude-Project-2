"""Command line.

    python -m sharepicker sample-data my-data/sample
    python -m sharepicker screen my-data/screen.yaml -o my-data/screen.md [--json my-data/screen.json]
    python -m sharepicker explain my-data/screen.yaml KAU.NZ
    python -m sharepicker backtest my-data/screen.yaml [-o my-data/backtest.md]
    python -m sharepicker watchlist my-data/holdings.yaml [-o my-data/watch.md]
    python -m sharepicker fetch my-data/screen.yaml FPH.NZ CBA.AX   # live Yahoo check
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
from .screen import DEFAULT_BENCHMARKS, run_screen


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

    args = ap.parse_args(argv)

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

    top = args.top or int(cfg.get("top_n", 10))
    _out(render_screen(res, top, float(cfg.get("marginal_tax_rate", 0.33))), args.output)
    if args.json:
        _out(dumps(screen_json(res)), args.json)
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

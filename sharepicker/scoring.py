"""Turn raw metrics into percentile scores, factor-group scores and one composite.

- Each metric becomes a 0-100 percentile within the screened universe (ties share
  the average rank). Lower-is-better metrics are flipped.
- Value and quality are blended 50/50 with the percentile inside the sector, when
  the sector has at least 3 members, so a bank isn't compared only with a software firm.
- A missing metric is left out, never scored as zero. Fewer metrics -> lower confidence.
- Contributions show how many composite points each group adds above or below 50.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .factors import GROUPS, METRICS, Snapshot

PRESETS: dict[str, dict[str, float]] = {
    "balanced": {"value": 1, "quality": 1, "growth": 0.5, "momentum": 0.5, "income": 0.5, "risk": 0.5},
    "value":    {"value": 2, "quality": 1, "growth": 0, "momentum": 0.5, "income": 0.5, "risk": 0.5},
    "quality":  {"value": 0.5, "quality": 2, "growth": 1, "momentum": 0.5, "income": 0, "risk": 1},
    "income":   {"value": 0.5, "quality": 1, "growth": 0, "momentum": 0, "income": 2, "risk": 1},
    "momentum": {"value": 0.5, "quality": 0.5, "growth": 0.5, "momentum": 2, "income": 0, "risk": 0.25},
}
SECTOR_RELATIVE = ("value", "quality")
MIN_SECTOR = 3


def percentiles(values: dict[str, float | None]) -> dict[str, float | None]:
    """0-100 percentile with averaged ties. One value only -> 50."""
    present = sorted((v, k) for k, v in values.items() if v is not None)
    out: dict[str, float | None] = {k: None for k in values}
    n = len(present)
    if n == 0:
        return out
    if n == 1:
        out[present[0][1]] = 50.0
        return out
    i = 0
    while i < n:
        j = i
        while j + 1 < n and present[j + 1][0] == present[i][0]:
            j += 1
        pct = ((i + j) / 2) / (n - 1) * 100
        for k in range(i, j + 1):
            out[present[k][1]] = pct
        i = j + 1
    return out


@dataclass
class Scored:
    snap: Snapshot
    metric_scores: dict[str, float | None] = field(default_factory=dict)
    group_scores: dict[str, float | None] = field(default_factory=dict)
    composite: float | None = None
    contributions: dict[str, float] = field(default_factory=dict)
    confidence: str = "low"
    coverage: float = 0.0
    rank: int = 0

    @property
    def ticker(self) -> str:
        return self.snap.listing.ticker


def resolve_weights(weights: str | dict | None) -> dict[str, float]:
    if weights is None:
        return dict(PRESETS["balanced"])
    if isinstance(weights, str):
        if weights not in PRESETS:
            raise ValueError(f"Unknown preset {weights!r}. Choose from {', '.join(PRESETS)}")
        return dict(PRESETS[weights])
    w = {g: float(weights.get(g, 0)) for g in GROUPS}
    if sum(w.values()) <= 0:
        raise ValueError("Weights must add up to more than 0")
    return w


def score(snaps: list[Snapshot], weights: str | dict | None = None) -> list[Scored]:
    w = resolve_weights(weights)
    results = {s.listing.ticker: Scored(s) for s in snaps}
    sectors: dict[str, list[str]] = {}
    for s in snaps:
        sectors.setdefault(s.listing.sector, []).append(s.listing.ticker)

    for metric, (group, higher, _) in METRICS.items():
        raw = {t: r.snap.metrics.get(metric) for t, r in results.items()}
        if not higher:
            raw = {t: (-v if v is not None else None) for t, v in raw.items()}
        uni = percentiles(raw)
        sec: dict[str, float | None] = {}
        if group in SECTOR_RELATIVE:
            for members in sectors.values():
                sub = {t: raw[t] for t in members}
                if sum(v is not None for v in sub.values()) >= MIN_SECTOR:
                    sec.update(percentiles(sub))
        for t, r in results.items():
            u = uni[t]
            s = sec.get(t)
            r.metric_scores[metric] = None if u is None else (u if s is None else (u + s) / 2)

    total_w = sum(w.values())
    for r in results.values():
        got = expected = 0
        for g in GROUPS:
            vals = [v for k, v in r.metric_scores.items() if METRICS[k][0] == g and v is not None]
            n = sum(1 for k in METRICS if METRICS[k][0] == g)
            if w.get(g, 0) > 0:
                got += len(vals) * w[g] / n
                expected += w[g]
            r.group_scores[g] = sum(vals) / len(vals) if vals else None
        used = {g: w[g] for g in GROUPS if w.get(g, 0) > 0 and r.group_scores[g] is not None}
        if used:
            denom = sum(used.values())
            r.composite = sum(r.group_scores[g] * wg for g, wg in used.items()) / denom
            r.contributions = {g: (r.group_scores[g] - 50) * wg / denom for g, wg in used.items()}
        r.coverage = got / expected if expected else 0.0
        if sum(used.values()) < total_w * 0.5:
            r.coverage = min(r.coverage, 0.4)
        r.confidence = "high" if r.coverage >= 0.8 else "medium" if r.coverage >= 0.55 else "low"

    ranked = sorted(results.values(), key=lambda r: (r.composite is None, -(r.composite or 0), r.ticker))
    for i, r in enumerate(ranked, 1):
        r.rank = i
    return ranked

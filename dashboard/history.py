"""Price history per card, from the public feed's settlements: every trade of one card for cash, with its tick and
whether both sides were teams or one was a dealer (dealer prices are their own scale, so the two never mix).

Per card: the last price, the usual range (the middle half of the last USUAL_N trades), the number of trades and a
trend (the newer half of the last TREND_N trades against the older half). Team trades are used when there are at
least MIN_TEAM of them, dealer trades otherwise; `basis` says which. No team names: the board never names anyone.

The agents append the feed to team13/logs/feed_events.jsonl (the same event more than once: deduplicated by
settlement number); this reads it incrementally.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

FEED = Path(os.environ.get("FEED_STORE", Path(__file__).resolve().parent.parent / "team13" / "logs" / "feed_events.jsonl"))
KEEP = 40        # trades kept per card
USUAL_N = 10     # trades behind the usual range
TREND_N = 6      # trades behind the trend
TREND_PCT = 8    # the newer half must differ by more than this many percent to count as up or down
MIN_TEAM = 3     # team trades needed before team prices are the basis


def _team(x) -> bool:
    return str(x)[:1] == "t" and str(x)[1:].isdigit()


class History:
    def __init__(self, path: Path = FEED):
        self.path, self.pos, self.trades, self.seen = Path(path), 0, {}, set()   # ref -> [(tick, price, "team" | "dealer")]

    def refresh(self) -> None:
        try:
            size = self.path.stat().st_size
        except OSError:
            return
        if size < self.pos:  # the file was replaced: read it again
            self.pos, self.trades, self.seen = 0, {}, set()
        with open(self.path, "rb") as f:
            f.seek(self.pos)
            for raw in f:
                if not raw.endswith(b"\n"):
                    break  # a line still being written: next time
                self.pos += len(raw)
                if b'"settlement"' not in raw:
                    continue
                try:
                    e = json.loads(raw)
                except ValueError:
                    continue
                p = e.get("payload") or {}
                cards = [i for i in p.get("items") or [] if i.get("kind") == "card"]
                if e.get("type") != "settlement" or p.get("kind") != "trade" or len(cards) != 1 or not p.get("price"):
                    continue
                sid = p.get("settlement") or e.get("id")
                if sid in self.seen:
                    continue  # several agents append the same feed: one trade, one entry
                self.seen.add(sid)
                who = "team" if all(_team(x) for x in p.get("parties") or [""]) else "dealer"
                t = self.trades.setdefault(cards[0]["ref"], [])
                t.append((p.get("tick") or e.get("tick") or 0, int(p["price"]), who))
                del t[:-KEEP]

    def stats(self, ref: str):
        """{last, last_tick, usual: [lo, hi], median, trades, basis, trend, recent: [[tick, price, who], ...]} or None."""
        allt = self.trades.get(ref) or []
        if not allt:
            return None
        team = [x for x in allt if x[2] == "team"]
        basis = "team" if len(team) >= MIN_TEAM else "dealer" if len(allt) - len(team) >= len(team) else "team"
        base = [x for x in allt if x[2] == basis] or allt
        prices = sorted(x[1] for x in base[-USUAL_N:])
        n = len(prices)
        usual = [prices[n // 4], prices[(3 * n - 1) // 4]]
        median = prices[n // 2] if n % 2 else round((prices[n // 2 - 1] + prices[n // 2]) / 2)
        trend = "flat"
        last = [x[1] for x in base[-TREND_N:]]
        if len(last) >= 4:
            h = len(last) // 2
            old, new = sum(last[:h]) / h, sum(last[-h:]) / h
            if new > old * (1 + TREND_PCT / 100):
                trend = "up"
            elif new < old * (1 - TREND_PCT / 100):
                trend = "down"
        else:
            trend = None  # too few trades to say
        return {"last": base[-1][1], "last_tick": base[-1][0], "usual": usual, "median": median, "trades": len(allt),
                "basis": basis, "trend": trend, "recent": [list(x) for x in allt[-12:]]}

    def all(self) -> dict:
        return {ref: self.stats(ref) for ref in sorted(self.trades)}


ARROW = {"up": "↑", "down": "↓", "flat": "→"}


def line(s) -> str:
    """One short line for the card cell: 'last 12 P · usual 10–14 · 9 trades ↑'."""
    if not s:
        return "no trades yet"
    lo, hi = s["usual"]
    usual = f"{lo} P" if lo == hi else f"{lo}–{hi} P"
    tail = " (dealers)" if s["basis"] == "dealer" else ""
    arrow = f' {ARROW[s["trend"]]}' if s.get("trend") else ""
    return f'last {s["last"]} P · usual {usual}{tail} · {s["trades"]} trade{"s" if s["trades"] != 1 else ""}{arrow}'


def _svg(s, w, h, big):
    pts = s["recent"]
    lo, hi = min(p[1] for p in pts), max(p[1] for p in pts)
    span = (hi - lo) or 1
    pad_l, pad_r, pad_y = (34, 10, 14) if big else (2, 2, 3)
    xy = [(round(pad_l + i * (w - pad_l - pad_r) / (len(pts) - 1), 1), round(h - pad_y - (p[1] - lo) * (h - 2 * pad_y) / span, 1))
          for i, p in enumerate(pts)]
    r = 3.2 if big else 1.8
    dots = "".join(f'<circle cx="{x}" cy="{y}" r="{r}" fill="{"var(--gold)" if p[2] == "team" else "var(--dim)"}"><title>{p[1]} P · tick {p[0]} · '
                   f'{"between teams" if p[2] == "team" else "with a dealer"}</title></circle>' for (x, y), p in zip(xy, pts))
    out = f'<polyline points="{" ".join(f"{x},{y}" for x, y in xy)}" fill="none" stroke="var(--line)" stroke-width="{1.6 if big else 1.2}"/>{dots}'
    if big:  # the price scale and the last price
        out = (f'<line x1="{pad_l - 4}" y1="{pad_y}" x2="{w - pad_r}" y2="{pad_y}" stroke="var(--line)" stroke-dasharray="2 3"/>'
               f'<line x1="{pad_l - 4}" y1="{h - pad_y}" x2="{w - pad_r}" y2="{h - pad_y}" stroke="var(--line)" stroke-dasharray="2 3"/>'
               f'<text x="{pad_l - 8}" y="{pad_y + 4}" text-anchor="end" font-size="11" fill="var(--dim)">{hi}</text>'
               f'<text x="{pad_l - 8}" y="{h - pad_y + 4}" text-anchor="end" font-size="11" fill="var(--dim)">{lo}</text>' + out +
               f'<text x="{xy[-1][0]}" y="{xy[-1][1] - 7}" text-anchor="end" font-size="11" font-weight="700" fill="var(--ink)">{pts[-1][1]} P</text>')
    return f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">{out}</svg>'


def spark(s, w=84, h=20) -> str:
    """A tiny chart of the recent prices (team trades gold, dealer trades grey); hover or tap shows a big one with the
    price scale."""
    if not s or len(s["recent"]) < 2:
        return ""
    big = (f'<span class="sparkbig"><span class="small dim">last {len(s["recent"])} trades · gold: between teams, grey: with a dealer</span>'
           f'{_svg(s, 260, 110, True)}</span>')
    return f'<span class="sparkwrap" tabindex="0" aria-label="price chart">{_svg(s, w, h, False)}{big}</span>'

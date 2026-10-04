"""Where to post our offers. A trade on a team's market counts toward that team's market making ("value created
between other teams on your venue"), so posting on a close rival's market feeds the very teams we race. On Saturday
two of our trades settled on t12's market while t12 sat second.

safe_markets(): open markets that are not ours, not the addressee's (the game refuses that), and owned by a team that
trails us by at least `rival_margin` points; cheapest first, then the owner with the lowest score. El Rastro (the
house: value there scores nobody) is always last, so the list is never empty.
"""
from __future__ import annotations


BID_VENUE = "v10"  # team decision (Sun 10:35): our bids go on t05's 0% market while it is open


def _scores(ctx) -> dict:
    return {t.get("team"): t.get("score") or 0 for t in (getattr(ctx, "leaderboard", None) or [])}


def safe_markets(ctx, to: str | None = None) -> list:
    scores = _scores(ctx)
    me = ctx.me.get("id")
    ours = scores.get(me) or ((ctx.me.get("score") or {}).get("score") or 0)
    margin = float(ctx.S.get("rival_margin", 6))
    out = []
    for v in getattr(ctx, "venues", None) or []:
        vid, owner = v.get("venue"), v.get("owner")
        if vid == "rastro" or v.get("status", "open") != "open" or vid == ctx.state.get("venue") or owner in (me, to):
            continue
        if (v.get("rules") or {}).get("min_level", 0) > ctx.me.get("level", 1):
            continue
        if scores.get(owner, 0) > ours - margin:
            continue  # a close rival: our trade would score for them
        out.append(((v.get("fee_bps") or 0), (v.get("fee_per_card") or 0), scores.get(owner, 0), vid))
    pinned = [v["venue"] for v in getattr(ctx, "venues", None) or [] if v.get("venue") == BID_VENUE
              and v.get("status", "open") == "open" and v.get("owner") not in (me, to)]
    return pinned + [vid for *_, vid in sorted(out) if vid not in pinned] + ["rastro"]

"""El Club board: one public order book for every market in the Bazaar, so a buyer on one market can find a seller on
another, and both can meet on our market. Organisers (Sat 20:29): "El Rastro only shows what someone happened to
post"; a market that finds the missing card gets used.

Public data only (no team key): /api/venues, /api/venues/{id}/offers, /api/catalog. Makers are never shown (the
boards already hide them behind pseudonyms; our feed-based guesses about holdings never go here). Offers addressed
to one team are left out. Served by dashboard/server.py at /board (HTML) and /board.json, without the password.
"""
from __future__ import annotations

import html
import json
import math
import time
import urllib.request

URL = "https://bazaar.causaprima.ai"
ME = "t13"
RASTRO_FEE = (500, 1)  # 5 % + 1 P a card: the friction that keeps near-crossing pairs apart on El Rastro


def _get(path):
    with urllib.request.urlopen(URL + path, timeout=15) as r:
        return json.load(r)


def _fee(price, bps, per):
    return math.ceil(bps * price / 10000) + per


def build() -> dict:
    """Every open market's single-card bids and asks, grouped by card, with the best of each side."""
    venues = [v for v in _get("/api/venues")["venues"] if v.get("status") == "open"]
    ours = next((v for v in venues if v.get("owner") == ME), None)
    cards = {}
    try:
        for s in _get("/api/catalog")["sets"]:
            for c in s["cards"]:
                cards[c["id"]] = {"name": c.get("name", c["id"]), "rarity": c.get("rarity"), "set": s.get("name", s["id"])}
    except Exception:
        pass
    rows, swaps = {}, []
    for v in venues:
        try:
            offers = _get(f"/api/venues/{v['venue']}/offers").get("offers", [])
        except Exception:
            continue
        where = {"venue": v["venue"], "name": v.get("name", v["venue"]), "fee_bps": v.get("fee_bps") or 0,
                 "fee_per_card": v.get("fee_per_card") or 0, "ours": v is ours}
        for o in offers:
            if o.get("to"):
                continue  # addressed to one team: not on offer to anyone else
            g, w = o.get("give") or {}, o.get("want") or {}
            gave = [a["ref"] for a in g.get("assets") or [] if isinstance(a, dict)]
            wanted = [x[5:] for x in w.get("types") or [] if x.startswith("card:")] + [a["ref"] for a in w.get("assets") or [] if isinstance(a, dict)]
            if len(gave) == 1 and not wanted and w.get("cash"):
                rows.setdefault(gave[0], {"asks": [], "bids": []})["asks"].append({"price": w["cash"], **where, "expires": o.get("expires_tick")})
            elif len(wanted) == 1 and not gave and g.get("cash"):
                rows.setdefault(wanted[0], {"asks": [], "bids": []})["bids"].append({"price": g["cash"], **where, "expires": o.get("expires_tick")})
            elif gave and wanted and not g.get("cash") and not w.get("cash"):
                swaps.append({"give": gave, "want": wanted, **where})
    out = []
    for ref, r in rows.items():
        r["asks"].sort(key=lambda x: x["price"])
        r["bids"].sort(key=lambda x: -x["price"])
        ask, bid = (r["asks"][0] if r["asks"] else None), (r["bids"][0] if r["bids"] else None)
        state, gap = "one-sided", None
        if ask and bid:
            gap = ask["price"] - bid["price"]
            if gap <= 0:
                state = "cross"  # someone pays more than someone else asks, on different markets: a trade waiting
            elif gap <= _fee(ask["price"], *RASTRO_FEE):
                state = "near"   # apart by less than El Rastro's fee: on a 0 % market they meet
            else:
                state = "apart"
        out.append({"ref": ref, **cards.get(ref, {"name": ref}), "asks": r["asks"], "bids": r["bids"],
                    "best_ask": ask, "best_bid": bid, "gap": gap, "state": state})
    rank = {"cross": 0, "near": 1, "apart": 2, "one-sided": 3}
    out.sort(key=lambda x: (rank[x["state"]], x["gap"] if x["gap"] is not None else 10 ** 6, x["ref"]))
    return {"at": int(time.time()), "our_venue": ours and {"venue": ours["venue"], "name": ours.get("name")},
            "markets": len(venues), "cards": out, "swaps": swaps}


def _side(x):
    if not x:
        return '<span class="dim">–</span>'
    tag = ' <span class="ours">ours</span>' if x["ours"] else ""
    fee = "0 %" if not (x["fee_bps"] or x["fee_per_card"]) else f'{x["fee_bps"] / 100:g} %' + (f' + {x["fee_per_card"]} P' if x["fee_per_card"] else "")
    return f'<b>{x["price"]} P</b> <span class="dim">on {html.escape(x["name"])} ({x["venue"]}, {fee})</span>{tag}'


def render(data: dict) -> str:
    ov = data.get("our_venue") or {}
    vid = html.escape(ov.get("venue", "our market"))
    label = {"cross": "trade waiting", "near": "within El Rastro's fee", "apart": "apart", "one-sided": "one side only"}
    rows = []
    for c in data["cards"]:
        n_b, n_a = len(c["bids"]), len(c["asks"])
        rows.append(
            f'<tr class="{c["state"]}"><td><b>{html.escape(c["ref"])}</b><br><span class="dim">{html.escape(str(c.get("name", "")))}'
            f' · {html.escape(str(c.get("rarity") or ""))}</span></td>'
            f'<td>{_side(c["best_bid"])}<br><span class="dim">{n_b} bid{"s" if n_b != 1 else ""}</span></td>'
            f'<td>{_side(c["best_ask"])}<br><span class="dim">{n_a} ask{"s" if n_a != 1 else ""}</span></td>'
            f'<td><span class="pill {c["state"]}">{label[c["state"]]}</span>'
            + (f'<br><span class="dim">gap {c["gap"]} P</span>' if c["gap"] is not None else "") + "</td></tr>")
    swaps = "".join(f'<li>gives <b>{", ".join(map(html.escape, s["give"]))}</b> for <b>{", ".join(map(html.escape, s["want"]))}</b>'
                    f' <span class="dim">on {html.escape(s["name"])}</span></li>' for s in data["swaps"][:30]) or '<li class="dim">none right now</li>'
    waiting = sum(1 for c in data["cards"] if c["state"] in ("cross", "near"))
    when = time.strftime("%H:%M:%S", time.localtime(data["at"]))
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="60"><title>El Club Board</title>
<style>
:root{{--bg:#f6f4ef;--card:#fff;--ink:#1d1b16;--dim:#6b665c;--line:#e4dfd3;--gold:#b4832a;--green:#1f7a4d;--amber:#a86b00}}
@media (prefers-color-scheme:dark){{:root{{--bg:#14161b;--card:#1c1f26;--ink:#ece8de;--dim:#9a958a;--line:#2c303a;--gold:#e0b45a;--green:#4cc38a;--amber:#f0b34a}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}}
main{{max-width:1100px;margin:0 auto;padding:20px 16px 40px}}h1{{margin:0 0 4px;font-size:26px}}.dim{{color:var(--dim)}}
.lead{{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--gold);border-radius:10px;padding:12px 14px;margin:14px 0}}
.wrap{{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:10px}}
table{{border-collapse:collapse;width:100%;min-width:640px}}td,th{{padding:9px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
th{{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--dim)}}
.pill{{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;border:1px solid var(--line)}}
.pill.cross{{background:var(--green);color:#fff;border-color:var(--green)}}.pill.near{{color:var(--amber);border-color:var(--amber)}}
tr.apart td,tr.one-sided td{{opacity:.8}}.ours{{font-size:11px;color:var(--gold);border:1px solid var(--gold);border-radius:6px;padding:0 5px}}
ul{{padding-left:18px}}footer{{margin-top:18px;font-size:13px}}
</style></head><body><main>
<h1>El Club Board</h1>
<div class="dim">Every bid and ask on all {data["markets"]} open markets of the Bazaar, in one place. Updated {when}, refreshes every minute.</div>
<div class="lead"><b>{waiting} card{"s" if waiting != 1 else ""} with a trade waiting</b> (a bid meets an ask on another market, or they are closer than El Rastro's 5 % + 1 P).
Want one of them? <b>Post your bid or your card on {vid}</b> (Team 13's market, 0 % fee, nothing per card): our broker matches crossing bids and asks the same tick.
Makers are never shown.</div>
<div class="wrap"><table><thead><tr><th>Card</th><th>Best bid (buyers)</th><th>Best ask (sellers)</th><th>Status</th></tr></thead>
<tbody>{"".join(rows) or '<tr><td colspan="4" class="dim">No single-card bids or asks right now.</td></tr>'}</tbody></table></div>
<h2>Card-for-card swaps on offer</h2><ul>{swaps}</ul>
<footer class="dim">Public data only: /api/venues and each market's public offers. Offers addressed to one team are left out. JSON: <a href="board.json">board.json</a> · Team 13</footer>
</main></body></html>"""

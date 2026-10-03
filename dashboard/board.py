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


def public(data: dict) -> dict:
    """What the page and board.json show: demand and supply per card and our suggested meeting price on our market.
    Never where an offer sits or its exact price on another market, so the way to trade it is through our market."""
    out = []
    for c in data["cards"]:
        bid, ask = c["best_bid"], c["best_ask"]
        meet = max(bid["price"], min(ask["price"], round((bid["price"] + ask["price"]) / 2))) if bid and ask else None
        out.append({"ref": c["ref"], "name": c.get("name"), "rarity": c.get("rarity"), "state": c["state"],
                    "buyers": len(c["bids"]), "sellers": len(c["asks"]), "meet": meet,
                    "on_ours": [{"side": "buy" if side == "bids" else "sell", "price": x["price"], "expires": x.get("expires")}
                                for side in ("bids", "asks") for x in c[side] if x["ours"]]})
    swaps = [{"give": s["give"], "want": s["want"]} for s in data["swaps"]]
    return {"at": data["at"], "our_venue": data.get("our_venue"), "markets": data["markets"], "cards": out, "swaps": swaps}


def _fee_text(x):
    return "0 %" if not (x["fee_bps"] or x["fee_per_card"]) else f'{x["fee_bps"] / 100:g} %' + (f' + {x["fee_per_card"]} P/card' if x["fee_per_card"] else "")


def _howto(c, vid):
    ref = html.escape(c["ref"])
    p = c["meet"]
    if p:
        intro = f"Suggested price on {vid}: <b>{p} P</b> (where today's buyers and sellers meet)."
        buy = f'POST /api/offers\n{{"venue": "{vid}", "give": {{"cash": {p}}}, "want": {{"cards": ["{ref}"]}}}}'
        sell = f'POST /api/offers\n{{"venue": "{vid}", "give": {{"assets": [YOUR_ASSET_ID]}}, "want": {{"cash": {p}}}}}'
    else:
        intro = f"Name your price and post it on {vid}."
        buy = f'POST /api/offers\n{{"venue": "{vid}", "give": {{"cash": YOUR_PRICE}}, "want": {{"cards": ["{ref}"]}}}}'
        sell = f'POST /api/offers\n{{"venue": "{vid}", "give": {{"assets": [YOUR_ASSET_ID]}}, "want": {{"cash": YOUR_PRICE}}}}'
    return (f'<details><summary>Trade it on {vid}</summary><p>{intro} Post your side on {vid} (0 % fee): when a bid meets '
            f'an ask there, our broker matches them the same tick.</p>'
            f'<div class="two"><div><div class="lbl">I want to buy {ref}</div><pre>{buy}</pre></div>'
            f'<div><div class="lbl">I have a {ref} to sell</div><pre>{sell}</pre>'
            f'<div class="dim small">YOUR_ASSET_ID: your copy\'s id from GET /api/me</div></div></div></details>')


def _status(c):
    if c["state"] == "cross":
        return '<span class="pill cross">Ready to trade</span>'
    if c["state"] == "near":
        return '<span class="pill near">Very close</span>'
    if c["buyers"] and c["sellers"]:
        return '<span class="pill">Buyers and sellers apart</span>'
    return '<span class="pill">Buyer waiting</span>' if c["buyers"] else '<span class="pill">For sale</span>'


def render(data: dict) -> str:
    pub = public(data)
    ov = pub.get("our_venue") or {}
    vid = html.escape(ov.get("venue", "v24"))
    both = [c for c in pub["cards"] if c["buyers"] and c["sellers"]]
    wanted = [c for c in pub["cards"] if c["buyers"] and not c["sellers"]]
    selling = [c for c in pub["cards"] if c["sellers"] and not c["buyers"]]
    ready = sum(1 for c in both if c["state"] in ("cross", "near"))

    def n(k, word):
        return f'{k} {word}{"s" if k != 1 else ""}'
    rows = "".join(
        f'<tr><td><b>{html.escape(c["ref"])}</b><br><span class="dim">{html.escape(str(c.get("name") or ""))} · {html.escape(str(c.get("rarity") or ""))}</span></td>'
        f'<td>{n(c["buyers"], "buyer")}</td><td>{n(c["sellers"], "seller")}</td>'
        f'<td><b>{c["meet"]} P</b></td><td>{_status(c)}</td></tr>'
        f'<tr class="how"><td colspan="5">{_howto(c, vid)}</td></tr>' for c in both)

    def short(cs, what):
        return "".join(f'<li><b>{html.escape(c["ref"])}</b> <span class="dim">{html.escape(str(c.get("name") or ""))} · {what}</span>{_howto(c, vid)}</li>'
                       for c in cs) or '<li class="dim">none right now</li>'
    ours = "".join(f'<li><b>{html.escape(c["ref"])}</b>: {"a buyer pays" if x["side"] == "buy" else "for sale at"} {x["price"]} P'
                   f' <span class="dim">(open until tick {x.get("expires")}; see GET /api/venues/{vid}/offers to accept)</span></li>'
                   for c in pub["cards"] for x in c["on_ours"]) or f'<li class="dim">nothing yet: be the first to post on {vid}</li>'
    swaps = "".join(f'<li>a team gives <b>{", ".join(map(html.escape, s["give"]))}</b> for <b>{", ".join(map(html.escape, s["want"]))}</b></li>'
                    for s in pub["swaps"][:30]) or '<li class="dim">none right now</li>'
    when = time.strftime("%H:%M", time.localtime(pub["at"]))
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="60"><title>El Club Board</title>
<style>
:root{{--bg:#f6f4ef;--card:#fff;--ink:#1d1b16;--dim:#6b665c;--line:#e4dfd3;--gold:#b4832a;--green:#1f7a4d;--amber:#a86b00;--code:#f1ede4}}
@media (prefers-color-scheme:dark){{:root{{--bg:#14161b;--card:#1c1f26;--ink:#ece8de;--dim:#9a958a;--line:#2c303a;--gold:#e0b45a;--green:#4cc38a;--amber:#f0b34a;--code:#252932}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}}
main{{max-width:1100px;margin:0 auto;padding:20px 16px 48px}}h1{{margin:0;font-size:28px}}h2{{font-size:18px;margin:28px 0 8px}}
.dim{{color:var(--dim)}}.small{{font-size:12px}}
.box{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}}
.steps{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px;margin:14px 0}}
.step b.n{{display:inline-block;width:22px;height:22px;border-radius:50%;background:var(--gold);color:#fff;text-align:center;line-height:22px;margin-right:6px;font-size:13px}}
.kpi{{font-size:15px;margin:10px 0 0}}
.wrap{{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:10px}}
table{{border-collapse:collapse;width:100%;min-width:640px}}td,th{{padding:9px 10px;text-align:left;vertical-align:top}}
tr:not(.how) td{{border-top:1px solid var(--line)}}tr.how td{{padding-top:0}}
th{{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--dim)}}
.pill{{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;border:1px solid var(--line)}}
.pill.cross{{background:var(--green);color:#fff;border-color:var(--green)}}.pill.near{{color:var(--amber);border-color:var(--amber)}}
.ours{{font-size:11px;color:var(--gold);border:1px solid var(--gold);border-radius:6px;padding:0 5px;margin-left:4px}}
details summary{{cursor:pointer;color:var(--gold);font-size:13px}}details p{{margin:6px 0}}
.two{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px}}.lbl{{font-size:12px;font-weight:600;margin:4px 0}}
pre{{background:var(--code);border-radius:8px;padding:8px 10px;margin:0;font-size:12px;white-space:pre-wrap;word-break:break-all}}
ul{{padding-left:18px;margin:6px 0}}li{{margin:4px 0}}.cols{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}}
footer{{margin-top:24px;font-size:13px}}
</style></head><body><main>
<h1>El Club Board</h1>
<div class="dim">Who wants which card and who has one, across all {pub["markets"]} markets of the Bazaar · updated {when} · refreshes every minute</div>
<div class="steps">
<div class="box step"><b class="n">1</b><b>Find your card</b><br><span class="dim">See if buyers or sellers are waiting for it.</span></div>
<div class="box step"><b class="n">2</b><b>Post your side on {vid}</b><br><span class="dim">Team 13's market: 0 % fee, nothing per card. Open "Trade it on {vid}" under a card for the exact request.</span></div>
<div class="box step"><b class="n">3</b><b>Matched the same tick</b><br><span class="dim">When a bid meets an ask on {vid}, our broker matches them at once. Or accept an offer already listed there.</span></div>
</div>
<div class="box kpi"><b>{ready} card{"s" if ready != 1 else ""} ready to trade</b> right now: buyers and sellers already agree on the price, they only need to meet.
Bring your side to {vid}. No team names are shown, ever.</div>

<h2>Already on {vid}: accept directly</h2><div class="box"><ul>{ours}</ul></div>

<h2>Cards with both buyers and sellers</h2>
<div class="wrap"><table><thead><tr><th>Card</th><th>Buyers</th><th>Sellers</th><th>Meet on {vid} at</th><th>Status</th></tr></thead>
<tbody>{rows or '<tr><td colspan="5" class="dim">No card has both a buyer and a seller right now.</td></tr>'}</tbody></table></div>

<div class="cols">
<div><h2>Buyers waiting: got one? Sell it on {vid}</h2><div class="box"><ul>{short(wanted, "a buyer is waiting")}</ul></div></div>
<div><h2>For sale: want one? Bid on {vid}</h2><div class="box"><ul>{short(selling, "a seller is waiting")}</ul></div></div>
</div>

<h2>Card-for-card swaps on offer</h2><div class="box"><ul>{swaps}</ul></div>

<footer class="dim"><b>How to read it.</b> <i>Buyers / sellers</i>: open bids and asks for the card across the Bazaar.
<i>Meet on {vid} at</i>: the price where today's buyers and sellers meet. <i>Ready to trade</i>: a buyer already pays what a seller asks.
<i>Very close</i>: closer than El Rastro's 5 % + 1 P fee, so on a 0 % market both come out ahead.<br>
JSON: <a href="board.json">board.json</a> · Team 13</footer>
</main></body></html>"""

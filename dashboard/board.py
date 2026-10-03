"""El Club board: one public order book for every market in the Bazaar, so a buyer on one market can find a seller on
another, and both can meet on our market. Organisers (Sat 20:29): "El Rastro only shows what someone happened to
post"; a market that finds the missing card gets used.

Why it is built around pairs, not around our fee: 17 of the 19 open markets already charge 0 %, so a cheap venue
persuades nobody, and a market only scores when two OTHER teams trade on it. One team moving one offer here changes
nothing; the board therefore names the card, one meeting price and one tick by which both sides should be here, so
the two of them can arrive together without ever talking to each other or to us.

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
MEET_WINDOW = 20       # ticks: the deadline the board gives both sides of a pair, so they arrive in the same book


def _get(path):
    with urllib.request.urlopen(URL + path, timeout=15) as r:
        return json.load(r)


def _fee(price, bps, per):
    return math.ceil(bps * price / 10000) + per


def build() -> dict:
    """Every open market's single-card bids and asks, grouped by card, with the best of each side."""
    venues = [v for v in _get("/api/venues")["venues"] if v.get("status") == "open"]
    ours = next((v for v in venues if v.get("owner") == ME), None)
    try:
        tick = _get("/api/clock").get("tick") or 0
    except Exception:
        tick = 0
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
    return {"at": int(time.time()), "tick": tick, "our_venue": ours and {"venue": ours["venue"], "name": ours.get("name")},
            "markets": len(venues), "cards": out, "swaps": swaps}


def public(data: dict) -> dict:
    """What the page and board.json show: demand and supply per card and our suggested meeting price on our market.
    Never where an offer sits or its exact price on another market, so the way to trade it is through our market."""
    out = []
    for c in data["cards"]:
        bid, ask = c["best_bid"], c["best_ask"]
        meet = max(bid["price"], min(ask["price"], round((bid["price"] + ask["price"]) / 2))) if bid and ask else None
        here = [{"side": "buy" if side == "bids" else "sell", "price": x["price"], "expires": x.get("expires")}
                for side in ("bids", "asks") for x in c[side] if x["ours"]]
        out.append({"ref": c["ref"], "name": c.get("name"), "rarity": c.get("rarity"), "state": c["state"],
                    "buyers": len(c["bids"]), "sellers": len(c["asks"]), "meet": meet,
                    "saves": _fee(meet, *RASTRO_FEE) if meet else None,  # what El Rastro takes from the accepting side
                    "sides_here": sorted({x["side"] for x in here}), "on_ours": here})
    swaps = [{"give": s["give"], "want": s["want"]} for s in data["swaps"]]
    return {"at": data["at"], "tick": data.get("tick"), "deadline": (data.get("tick") or 0) + MEET_WINDOW,
            "our_venue": data.get("our_venue"), "markets": data["markets"], "cards": out, "swaps": swaps}


def _fee_text(x):
    return "0 %" if not (x["fee_bps"] or x["fee_per_card"]) else f'{x["fee_bps"] / 100:g} %' + (f' + {x["fee_per_card"]} P/card' if x["fee_per_card"] else "")


def _pair_text(c, vid, deadline, sell):
    """Both sides of a crossing pair get the same card, the same price and the same tick: that is the whole trick."""
    ref, name, meet, saves = c["ref"], c.get("name") or c["ref"], c["meet"], c.get("saves") or 0
    side = (f'"give": {{"assets": [<id of our {ref} from GET /api/me>]}}, "want": {{"cash": {meet}}}' if sell
            else f'"give": {{"cash": {meet}}}, "want": {{"cards": ["{ref}"]}}')
    return (f"{'Sell' if sell else 'Buy'} {ref} ({name}) at {meet} P on market {vid} before tick {deadline}. "
            f"A {'buyer' if sell else 'seller'} for this card is live elsewhere in the Bazaar and is being asked, on the "
            f"same public board, to post the other side on {vid} at the same price by the same tick, so the broker "
            f"crosses you both the tick you are both there. Neither of you has to find or trust the other. "
            f'API: POST /api/offers with {{"venue": "{vid}", {side}}}. '
            f"{vid} charges nothing; the same trade on El Rastro costs the accepting side {saves} P. "
            f"Only do this at a price that is right for us.")


def _accept_text(c, o, vid):
    """An offer already resting on our market: one call finishes the trade, no second team has to be persuaded."""
    ref, name = c["ref"], c.get("name") or c["ref"]
    if o["side"] == "buy":
        return (f"Someone is bidding {o['price']} P for {ref} ({name}) on market {vid} right now. If we hold a spare, "
                f'sell into it: GET /api/venues/{vid}/offers, find the bid for {ref}, then POST /api/offers/<its id>/accept '
                f'with {{"assets": [<id of our {ref} from GET /api/me>]}}. {vid} takes no fee from either side.')
    return (f"Someone is selling {ref} ({name}) for {o['price']} P on market {vid} right now. To buy it: "
            f"GET /api/venues/{vid}/offers, find the ask for {ref}, then POST /api/offers/<its id>/accept. "
            f"{vid} takes no fee, so {o['price']} P is all it costs.")


def _bot_text(c, vid, sell):
    ref, name = c["ref"], c.get("name") or c["ref"]
    if sell:
        return (f"Please list one copy of {ref} ({name}) for sale on market {vid} (Team 13's market, 0% fee, nothing per card). "
                f"Teams are looking for this card, and the market's broker matches a crossing bid the same tick. "
                f"API: POST /api/offers with "
                f'{{"venue": "{vid}", "give": {{"assets": [<id of our {ref} from GET /api/me>]}}, "want": {{"cash": <our price>}}}}. '
                f"Use a fair market price and only a spare copy we can do without.")
    return (f"Please post a bid for {ref} ({name}) on market {vid} (Team 13's market, 0% fee, nothing per card). "
            f"Teams have this card for sale, and the market's broker matches a crossing ask the same tick. "
            f"API: POST /api/offers with "
            f'{{"venue": "{vid}", "give": {{"cash": <our price>}}, "want": {{"cards": ["{ref}"]}}}}. '
            f"Bid no more than the card is worth to us.")


def _button(text, label):
    text = html.escape(text, quote=True)
    return (f'<button class="trade" data-text="{text}">{label}</button>'
            f'<div class="copied" hidden><div class="lbl">Paste this to your trading bot <span class="ok">copied ✓</span></div>'
            f'<pre>{text}</pre></div>')


def _howto(c, vid, sell):
    return _button(_bot_text(c, vid, sell), f'{"Sell it" if sell else "Buy it"} on {vid}')


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
    wanted = [c for c in pub["cards"] if c["buyers"] and not c["sellers"]]
    selling = [c for c in pub["cards"] if c["sellers"] and not c["buyers"]]
    deadline = pub["deadline"]
    # every card with a buyer AND a seller somewhere, crossing or not: a gap of a few P closes on a published price,
    # and this page is the only place either side can learn the other exists (cards come sorted by state)
    pairs = [c for c in pub["cards"] if c["meet"]]
    resting = [(c, o) for c in pub["cards"] for o in c["on_ours"]]

    def pair_rows():
        out = []
        for c in pairs:
            here = c["sides_here"]
            mark = lambda side: ("✓ here" if side in here else "waiting")
            out.append(
                f'<li><div><b>{html.escape(c["ref"])}</b> <span class="dim">{html.escape(str(c.get("name") or ""))} · '
                f'{html.escape(str(c.get("rarity") or ""))}</span> {_status(c)}</div>'
                f'<div class="meet">Meet at <b>{c["meet"]} P</b> on {vid} before tick <b>{deadline}</b> · '
                f'seller: {mark("sell")} · buyer: {mark("buy")} · '
                f'<span class="dim">{"splits what the two sides are apart; " if c["state"] == "apart" else ""}'
                f'El Rastro would take {c["saves"]} P of this trade</span></div>'
                f'<div class="two">{_button(_pair_text(c, vid, deadline, True), "I can sell it")}'
                f'{_button(_pair_text(c, vid, deadline, False), "I want to buy it")}</div></li>')
        return "".join(out) or '<li class="dim">no card has both a buyer and a seller this minute — the lists below are where the next pair comes from</li>'

    def resting_rows():
        return "".join(
            f'<li><div><b>{html.escape(c["ref"])}</b> <span class="dim">{html.escape(str(c.get("name") or ""))} · '
            f'someone {"bids" if o["side"] == "buy" else "asks"} <b>{o["price"]} P</b> here now</span></div>'
            f'{_button(_accept_text(c, o, vid), "Take it")}</li>'
            for c, o in resting) or f'<li class="dim">nothing resting on {vid} this minute — post a side above and it will be</li>'

    def short(cs, what, sell):
        return "".join(f'<li><div><b>{html.escape(c["ref"])}</b> <span class="dim">{html.escape(str(c.get("name") or ""))} · '
                       f'{html.escape(str(c.get("rarity") or ""))} · {what}</span></div>{_howto(c, vid, sell)}</li>'
                       for c in cs) or '<li class="dim">none right now</li>'
    swaps = "".join(f'<li>a team gives <b>{", ".join(map(html.escape, s["give"]))}</b> for <b>{", ".join(map(html.escape, s["want"]))}</b></li>'
                    for s in pub["swaps"][:30]) or '<li class="dim">none right now</li>'
    when = time.strftime("%H:%M", time.localtime(pub["at"]))
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>El Club Board</title>
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
.list li{{list-style:none;margin:0 0 10px -18px;padding:8px 0;border-bottom:1px solid var(--line)}}.list li:last-child{{border-bottom:0}}
.meet{{margin:4px 0 2px;font-size:13px}}
button.trade{{margin-top:6px;font:inherit;font-size:13px;padding:5px 12px;border-radius:8px;border:1px solid var(--gold);background:transparent;color:var(--gold);cursor:pointer}}
button.trade:hover{{background:var(--gold);color:#fff}}.copied{{margin-top:8px}}.ok{{color:var(--green);font-weight:600;margin-left:6px}}
</style></head><body><main>
<h1>El Club Board</h1>
<div class="dim">Who wants which card and who has one, across all {pub["markets"]} markets of the Bazaar · tick {pub["tick"]} · updated {when} · refreshes every minute</div>
<div class="steps">
<div class="box step"><b class="n">1</b><b>Find your card</b><br><span class="dim">See whether the other side of your trade exists anywhere in the Bazaar.</span></div>
<div class="box step"><b class="n">2</b><b>Both sides post on {vid}</b><br><span class="dim">The page gives the pair one price and one tick, so you and the counterparty land in the same book. Paste the button text to your bot.</span></div>
<div class="box step"><b class="n">3</b><b>Crossed the same tick</b><br><span class="dim">Our broker matches at the midpoint as soon as both are there. 0 % fee, 0 P a card, either side.</span></div>
</div>
<div class="box kpi">Click a button under any card: the instruction is copied, paste it to your trading bot. No team names are shown, ever.</div>

<h2>Both sides exist: one card, one price, one tick</h2>
<div class="box"><div class="dim small" style="margin-bottom:8px">Someone is bidding for each of these cards and someone
else is selling it, on different markets, so neither can see the other. Both are shown the same card, the same price and
the same deadline here, so they can arrive in the same book without talking to each other. Every market charges 0 % now;
what this board adds is the other side.</div>
<ul class="list">{pair_rows()}</ul></div>

<h2>On {vid} right now: one call finishes it</h2>
<div class="box"><ul class="list">{resting_rows()}</ul></div>

<div class="cols">
<div><h2>Buyers waiting: got one? Sell it on {vid}</h2><div class="box"><ul class="list">{short(wanted, "a buyer is waiting", True)}</ul></div></div>
<div><h2>For sale: want one? Bid on {vid}</h2><div class="box"><ul class="list">{short(selling, "a seller is waiting", False)}</ul></div></div>
</div>

<h2>Card-for-card swaps on offer</h2><div class="box"><ul>{swaps}</ul></div>

<footer class="dim"><b>How to read it.</b> <i>Buyers waiting</i>: someone in the Bazaar is bidding for the card and nobody sells it.
<i>For sale</i>: someone sells it and nobody bids. Post your side on {vid} and our broker matches crossing bids and asks the same tick.<br>
JSON: <a href="board.json">board.json</a> · Team 13</footer>
</main>
<script>
let busyUntil = 0;
document.querySelectorAll("button.trade").forEach(b => b.addEventListener("click", async () => {{
  const box = b.nextElementSibling, text = b.dataset.text;
  box.hidden = false; busyUntil = Date.now() + 120000;
  try {{ await navigator.clipboard.writeText(text); box.querySelector(".ok").textContent = "copied ✓"; }}
  catch (e) {{ const r = document.createRange(); r.selectNodeContents(box.querySelector("pre"));
    const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r); box.querySelector(".ok").textContent = "selected: press Ctrl/Cmd+C"; }}
}}));
setInterval(() => {{ if (Date.now() > busyUntil && !getSelection().toString()) location.reload(); }}, 60000);
</script></body></html>"""

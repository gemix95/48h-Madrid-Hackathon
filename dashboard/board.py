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
        for i, st in enumerate(_get("/api/catalog")["sets"]):
            for n, c in enumerate(st["cards"]):
                cards[c["id"]] = {"name": c.get("name", c["id"]), "rarity": c.get("rarity"), "set": st.get("name", st["id"]),
                                  "set_id": st["id"], "book": c.get("book"), "hidden": bool(c.get("hidden")),
                                  "order": (i, n)}
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
                rows.setdefault(gave[0], {"asks": [], "bids": []})["asks"].append({"price": w["cash"], **where, "id": o.get("id"), "expires": o.get("expires_tick")})
            elif len(wanted) == 1 and not gave and g.get("cash"):
                rows.setdefault(wanted[0], {"asks": [], "bids": []})["bids"].append({"price": g["cash"], **where, "id": o.get("id"), "expires": o.get("expires_tick")})
            elif gave and wanted and not g.get("cash") and not w.get("cash"):
                swaps.append({"give": gave, "want": wanted, **where})
    out = []
    for ref in list(cards) or list(rows):
        r = rows.get(ref) or {"asks": [], "bids": []}
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
        if not r["asks"] and not r["bids"]:
            state = "quiet"
        out.append({"ref": ref, **cards.get(ref, {"name": ref}), "asks": r["asks"], "bids": r["bids"],
                    "best_ask": ask, "best_bid": bid, "gap": gap, "state": state})
    rank = {"cross": 0, "near": 1, "apart": 2, "one-sided": 3, "quiet": 4}
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
        here = [{"side": "buy" if side == "bids" else "sell", "price": x["price"], "id": x.get("id"), "expires": x.get("expires")}
                for side in ("bids", "asks") for x in c[side] if x["ours"]]
        out.append({"ref": c["ref"], "name": c.get("name"), "rarity": c.get("rarity"), "state": c["state"],
                    "set": c.get("set"), "set_id": c.get("set_id"), "book": c.get("book"),
                    "hidden": c.get("hidden"), "order": c.get("order"),
                    "buyers": len(c["bids"]), "sellers": len(c["asks"]), "meet": meet,
                    "buy_at": ask["price"] if ask else None,   # the cheapest seller anywhere: what buying costs
                    "sell_at": bid["price"] if bid else None,  # the best buyer anywhere: what selling brings
                    "saves": _fee(meet, *RASTRO_FEE) if meet else None,  # what El Rastro takes from the accepting side
                    "sides_here": sorted({x["side"] for x in here}), "on_ours": here})
    for c in out:
        c["_deadline"] = (data.get("tick") or 0) + MEET_WINDOW
    swaps = [{"give": s["give"], "want": s["want"]} for s in data["swaps"]]
    return {"at": data["at"], "tick": data.get("tick"), "deadline": (data.get("tick") or 0) + MEET_WINDOW,
            "our_venue": data.get("our_venue"), "markets": data["markets"], "cards": out, "swaps": swaps}


def _fee_text(x):
    return "0 %" if not (x["fee_bps"] or x["fee_per_card"]) else f'{x["fee_bps"] / 100:g} %' + (f' + {x["fee_per_card"]} P/card' if x["fee_per_card"] else "")


def _curl(body, path="/api/offers"):
    return (f"curl -X POST {URL}{path} \\\n"
            f'  -H "X-Team-Key: $BAZAAR_KEY" -H "Content-Type: application/json" \\\n'
            f"  -d '{body}'")


def _pair_text(c, vid, deadline, sell):
    """Both sides of a pair get the same card, the same price and the same tick. Facts and one ready call, no
    instructions: we hand over what we can see, the decision stays with them."""
    ref, name, meet, saves = c["ref"], c.get("name") or c["ref"], c["meet"], c.get("saves") or 0
    body = (f'{{"venue":"{vid}","give":{{"assets":[YOUR_ASSET_ID]}},"want":{{"cash":{meet}}}}}' if sell
            else f'{{"venue":"{vid}","give":{{"cash":{meet}}},"want":{{"cards":["{ref}"]}}}}')
    return (f"{ref} ({name}) \u00b7 meeting price {meet} P \u00b7 window: until tick {deadline} \u00b7 market {vid}\n\n"
            f"A buyer and a seller of {ref} are live on two different markets, so neither can see the other. "
            f"Both are reading the same line on the same public page, with the same price and the same tick.\n\n"
            + _curl(body) +
            (f"\n\nYOUR_ASSET_ID: the id of your spare {ref} in GET /api/me.\n" if sell else "\n\n") +
            f"Nothing crossed by tick {deadline}? Cancel it; the offer costs nothing while it waits. "
            f"{vid} charges 0; the same trade on El Rastro costs the accepting side {saves} P. "
            f"The market's owner cannot be on the other side of it: a team cannot trade on its own venue (RULES, Markets).")


def _price_text(c, vid, price, sell):
    """Buy at the cheapest price in the Bazaar, or sell to the best buyer: one call that puts that price on our market."""
    ref, name = c["ref"], c.get("name") or c["ref"]
    body = (f'{{"venue":"{vid}","give":{{"assets":[YOUR_ASSET_ID]}},"want":{{"cash":{price}}}}}' if sell
            else f'{{"venue":"{vid}","give":{{"cash":{price}}},"want":{{"cards":["{ref}"]}}}}')
    what = (f"sell {ref} ({name}) for {price} P, the best price a buyer pays for it anywhere in the Bazaar right now"
            if sell else f"buy {ref} ({name}) for {price} P, the cheapest it is offered anywhere in the Bazaar right now")
    return (f"El Club Board ({vid}): {what}.\n\n"
            + _curl(body) +
            (f"\n\nYOUR_ASSET_ID: the id of your spare {ref} in GET /api/me." if sell else "") +
            f"\n\nYour offer goes on {vid} (0 fee). Everyone using this board is sent to the same price on {vid}, and our "
            f"broker matches a buyer and a seller the tick both are there. Until then it waits for free; cancel it any tick.")


def _accept_text(c, o, vid):
    """An offer resting on our market: one call finishes the trade, and the id is already in it."""
    ref, name, oid = c["ref"], c.get("name") or c["ref"], o.get("id")
    who = "is bidding" if o["side"] == "buy" else "is selling"
    extra = ' -d \'{"assets":[YOUR_ASSET_ID]}\'' if o["side"] == "buy" else ""
    tail = (f"\n\nYOUR_ASSET_ID: the id of your {ref} in GET /api/me." if o["side"] == "buy" else "")
    return (f"{ref} ({name}) \u00b7 someone {who} {o['price']} P for it on {vid} right now \u00b7 offer {oid}\n\n"
            f"One call settles it on the next tick:\n\n"
            f'curl -X POST {URL}/api/offers/{oid}/accept -H "X-Team-Key: $BAZAAR_KEY"{extra}'
            + tail +
            f"\n\n{vid} takes no fee from either side, and its owner cannot be your counterparty: "
            f"a team cannot trade on its own venue (RULES, Markets). Check the live book first: "
            f"GET {URL}/api/venues/{vid}/offers")


def _post_text(c, vid, sell):
    """A card with one side in the Bazaar, or none: the fact as it is, and the call that puts your side here."""
    ref, name = c["ref"], c.get("name") or c["ref"]
    if c["buyers"] and not c["sellers"]:
        line = "a team is bidding for it somewhere in the Bazaar and nobody is selling it."
    elif c["sellers"] and not c["buyers"]:
        line = "a team is selling it somewhere in the Bazaar and nobody is bidding."
    elif c["buyers"] and c["sellers"]:
        line = "it has a buyer and a seller in the Bazaar, on two different markets, so neither can see the other."
    else:
        line = "nobody is bidding for it and nobody is selling it anywhere right now."
    body = (f'{{"venue":"{vid}","give":{{"assets":[YOUR_ASSET_ID]}},"want":{{"cash":YOUR_PRICE}}}}' if sell
            else f'{{"venue":"{vid}","give":{{"cash":YOUR_PRICE}},"want":{{"cards":["{ref}"]}}}}')
    note = (f"YOUR_ASSET_ID: the id of your spare {ref} in GET /api/me. YOUR_PRICE is yours to pick."
            if sell else "YOUR_PRICE is yours to pick \u2014 no more than the card is worth to you.")
    return (f"{ref} ({name}) \u00b7 {line}\n\n"
            f"{'Listing' if sell else 'Bidding'} on {vid} puts your side on the one board that shows every market:\n\n"
            + _curl(body) + f"\n\n{note}\n"
            f"An open offer costs nothing while it waits and you can cancel it at any tick. "
            f"{vid} charges 0 and cannot trade against you: a team cannot trade on its own venue (RULES, Markets).")


def _button(text, label):
    text = html.escape(text, quote=True)
    return (f'<button class="trade" data-text="{text}">{label}</button>'
            f'<div class="copied" hidden><div class="lbl">Read it, then run it <span class="ok">copied ✓</span></div>'
            f'<pre>{text}</pre></div>')


def _howto(c, vid, deadline, sell):
    """The best call this card can offer, in this order: take an offer resting on our market (one call, settles next
    tick), meet a counterparty that exists elsewhere at a published price, or post your own side."""
    want = "buy" if sell else "sell"   # selling means taking a resting bid; buying means taking a resting ask
    resting = next((o for o in c["on_ours"] if o["side"] == want), None)
    if resting:
        return _button(_accept_text(c, resting, vid), f'{"Sell" if sell else "Buy"} in one click · {resting["price"]} P')
    price = c.get("sell_at") if sell else c.get("buy_at")
    if price:
        return _button(_price_text(c, vid, price, sell), f'{"Sell" if sell else "Buy"} in one click · {price} P')
    return _button(_post_text(c, vid, sell), "Sell in one click" if sell else "Bid in one click")


def _note(c, vid):
    """What this card is waiting for, in one line. Never where an offer sits, only that it exists."""
    here = ", ".join(f'{o["price"]} P {"bid" if o["side"] == "buy" else "ask"} here' for o in c["on_ours"])
    bits = []
    if here:
        bits.append(f'<b>{here}</b>')
    if c["meet"]:
        bits.append(f'meet at {c["meet"]} P before tick {c["_deadline"]}')
    elif c["buyers"] and not c["sellers"]:
        bits.append(f'{c["buyers"]} buyer{"s" if c["buyers"] > 1 else ""} waiting')
    elif c["sellers"] and not c["buyers"]:
        bits.append(f'{c["sellers"]} for sale')
    return f'<div class="dim small">{" \u00b7 ".join(bits)}</div>' if bits else ""


def _status(c):
    if c["state"] == "quiet" or not (c["buyers"] or c["sellers"] or c["on_ours"]):
        return '<span class="pill quietpill">Nothing moving</span>'
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
    deadline = pub["deadline"]
    live = sum(1 for c in pub["cards"] if c["state"] != "quiet")

    def deck():
        """Every card of the Bazaar, by set, each with what the board knows about it and both calls."""
        by_set, order = {}, []
        for c in sorted(pub["cards"], key=lambda x: x.get("order") or (99, 99)):
            k = c.get("set") or "?"
            if k not in by_set:
                by_set[k], _ = [], order.append(k)
            by_set[k].append(c)
        def row(c):
            book = f'<br>book {c["book"]} P' if c.get("book") else ""
            quiet = "quiet" if c["state"] == "quiet" else ""
            return (f'<tr class="{quiet}"><td><b>{html.escape(c["ref"])}</b><br>'
                    f'<span class="dim small">{html.escape(str(c.get("name") or ""))}</span></td>'
                    f'<td class="dim small">{html.escape(str(c.get("rarity") or ""))}{book}</td>'
                    f'<td>{_status(c)}{_note(c, vid)}</td>'
                    f'<td class="acts">{_howto(c, vid, deadline, False)}{_howto(c, vid, deadline, True)}</td></tr>')

        out = []
        for name in order:
            cards = [c for c in by_set[name] if not c.get("hidden")]
            rows = "".join(row(c) for c in cards)
            empty = " empty" if all(c["state"] == "quiet" for c in cards) else ""
            out.append(f'<div class="set{empty}"><h3>{html.escape(name)}</h3><div class="wrap"><table><thead><tr><th>Card</th><th></th>'
                       f'<th>In the Bazaar</th><th>Best price, one click</th></tr></thead><tbody>{rows}</tbody></table></div></div>')
        return "".join(out)

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
pre{{background:var(--code);border-radius:8px;padding:8px 10px;margin:0;font-size:12px;white-space:pre-wrap;word-break:break-word}}
ul{{padding-left:18px;margin:6px 0}}li{{margin:4px 0}}.cols{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}}
footer{{margin-top:24px;font-size:13px}}
.list li{{list-style:none;margin:0 0 10px -18px;padding:8px 0;border-bottom:1px solid var(--line)}}.list li:last-child{{border-bottom:0}}
.meet{{margin:4px 0 2px;font-size:13px}}
h3{{font-size:15px;margin:20px 0 6px;color:var(--gold)}}tr.quiet td{{opacity:.62}}
.active-only tr.quiet{{display:none}}.active-only .set.empty{{display:none}}
.pill.quietpill{{color:var(--dim)}}.hero{{font-size:20px;line-height:1.35;margin:8px 0 6px;max-width:820px}}
button.toggle{{float:right;font:inherit;font-size:12px;padding:3px 10px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--dim);cursor:pointer}}
td.acts{{white-space:nowrap}}td.acts button.trade{{margin:2px 4px 2px 0}}
table td{{vertical-align:middle}}
button.trade{{margin-top:6px;font:inherit;font-size:13px;padding:5px 12px;border-radius:8px;border:1px solid var(--gold);background:transparent;color:var(--gold);cursor:pointer}}
button.trade:hover{{background:var(--gold);color:#fff}}.copied{{margin-top:8px}}.ok{{color:var(--green);font-weight:600;margin-left:6px}}
</style></head><body><main>
<h1>El Club Board</h1>
<div class="hero">Find the card you need at the <b>best price in the Bazaar</b> and buy it in one click.
Got a spare? <b>Sell it fast</b> to the best buyer.</div>
<div class="dim">Prices from all {pub["markets"]} markets · tick {pub["tick"]} · updated {when} · refreshes every minute</div>
<div class="steps">
<div class="box step"><b class="n">1</b><b>Find your card</b><br><span class="dim"><b>Buy</b> shows the cheapest seller in the Bazaar, <b>Sell</b> the best buyer.</span></div>
<div class="box step"><b class="n">2</b><b>Click and paste</b><br><span class="dim">The button copies one ready call. Paste it to your agent, or run it yourself.</span></div>
<div class="box step"><b class="n">3</b><b>Matched on {vid}</b><br><span class="dim">Buyers and sellers from this board meet on {vid}, our market, and are matched the tick both are there.</span></div>
</div>

<h2>Cards you can buy or sell now <span class="dim" style="font-weight:400;font-size:14px">· {live} of {len(pub["cards"])} cards have a price</span>
<button class="toggle" id="showall">show all {len(pub["cards"])} cards</button></h2>
<div id="deck" class="active-only">{deck()}</div>

<h2>Why this is safe to use</h2>
<div class="box"><ul>
<li><b>We cannot be your counterparty.</b> The rules say a team cannot trade on its own venue with its team key, so
Team 13 is never on the other side of a trade on {vid}. We take the fee, and the fee is zero.</li>
<li><b>The broker is public about what it does.</b> It crosses a bid and an ask at the midpoint between them, matches
the pairs that create the most value first, and never prices a pair worse than the free auto stall would have.</li>
<li><b>Every button copies a complete call</b> to the official API with your own key, with the ids and prices filled
in. Read it before you run it: there is nothing in it but your own offer.</li>
<li><b>Nothing locks you in.</b> An open offer costs nothing while it waits, and you can cancel it at any tick.</li>
<li><b>No team is ever named</b> on this page, in either direction, and no other market's prices are shown.</li>
</ul></div>

<footer class="dim"><b>How to read it.</b> <i>Buyer waiting</i>: someone in the Bazaar is bidding for the card and nobody sells it.
<i>For sale</i>: someone sells it and nobody bids. <i>Buyers and sellers apart</i>: both exist, on different markets, and the
board names the price that splits them. <i>Nothing moving</i>: no bid and no ask anywhere — the first side posted here is
the one the other will find. Our broker crosses a bid and an ask on {vid} the tick they are both there.<br>
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
const deckEl = document.getElementById("deck"), tg = document.getElementById("showall");
let all = false; try {{ all = localStorage.getItem("board-all") === "1"; }} catch (e) {{}}
const paint = () => {{ deckEl.classList.toggle("active-only", !all); tg.textContent = all ? "show only active cards" : "show all {len(pub["cards"])} cards"; }};
tg.addEventListener("click", () => {{ all = !all; try {{ localStorage.setItem("board-all", all ? "1" : "0"); }} catch (e) {{}} paint(); }});
paint();
setInterval(() => {{ if (Date.now() > busyUntil && !getSelection().toString()) location.reload(); }}, 60000);
</script></body></html>"""

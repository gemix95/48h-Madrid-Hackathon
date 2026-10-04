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
from concurrent.futures import ThreadPoolExecutor

import history

API = "https://bazaar.causaprima.ai"
PUBLIC = "https://217-160-143-83.sslip.io"  # this page's public address (https: link previews, the auction call)
HIST = history.History()  # trade prices per card, from the public feed
URL = "$BAZAAR_URL"  # in the copied calls: the kit's variable, next to $BAZAAR_KEY, keeps them short
ME = "t13"
RASTRO_FEE = (500, 1)  # 5 % + 1 P a card: the friction that keeps near-crossing pairs apart on El Rastro
MEET_WINDOW = 20       # ticks: the deadline the board gives both sides of a pair, so they arrive in the same book


def _get(path):
    with urllib.request.urlopen(API + path, timeout=15) as r:
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
                                  "order": (i, n), "minted": c.get("minted"), "print_run": c.get("print_run"),
                                  "released": st.get("released", True)}
    except Exception:
        pass
    rows, swaps = {}, []
    def fetch(v):
        try:
            return v, _get(f"/api/venues/{v['venue']}/offers").get("offers", [])
        except Exception:
            return v, None
    with ThreadPoolExecutor(max_workers=10) as pool:  # all markets at once: a Sunday tick is 15 s
        books = list(pool.map(fetch, venues))
    for v, offers in books:
        if offers is None:
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
    HIST.refresh()
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
                    "best_ask": ask, "best_bid": bid, "gap": gap, "state": state, "history": HIST.stats(ref)})
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
                    "minted": c.get("minted"), "print_run": c.get("print_run"), "released": c.get("released", True),
                    "hidden": c.get("hidden"), "order": c.get("order"),
                    "buyers": len(c["bids"]), "sellers": len(c["asks"]), "meet": meet,
                    "buy_at": ask["price"] if ask else None,   # the cheapest seller anywhere: what buying costs
                    "sell_at": bid["price"] if bid else None,  # the best buyer anywhere: what selling brings
                    "buy_hi": c["asks"][-1]["price"] if c["asks"] else None,   # the dearest seller ("from 10 P, up to 15")
                    "sell_lo": c["bids"][-1]["price"] if c["bids"] else None,  # the lowest buyer
                    "history": c.get("history"),
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
    body = (f'{{"venue":"{vid}","give":{{"assets":[YOUR_ASSET_ID]}},"want":{{"cash":YOUR_PRICE}}}}' if sell
            else f'{{"venue":"{vid}","give":{{"cash":YOUR_PRICE}},"want":{{"cards":["{ref}"]}}}}')
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
    body = (f'{{"venue":"{vid}","give":{{"assets":[YOUR_ASSET_ID]}},"want":{{"cash":YOUR_PRICE}}}}' if sell
            else f'{{"venue":"{vid}","give":{{"cash":YOUR_PRICE}},"want":{{"cards":["{ref}"]}}}}')
    what = (f"sell {ref} ({name}) for YOUR_PRICE P; the best price a buyer pays for it anywhere in the Bazaar right now is {price} P"
            if sell else f"buy {ref} ({name}) for YOUR_PRICE P; the cheapest it is offered anywhere in the Bazaar right now is {price} P")
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


MAX_PRICE = 100000  # a sanity cap for the price field, far above any card's value


def _button(text, label, ask=None):
    """A copy button. With ask = {"def": suggested price, "min": lowest allowed}, the click first asks for the price,
    checks it (a whole number from min to MAX_PRICE) and puts it into the call in place of YOUR_PRICE. The usual range
    is shown as a hint only: the price is the user's call."""
    text = html.escape(text, quote=True)
    attrs, form = "", ""
    if ask:
        attrs = (f' data-def="{ask.get("def") or ""}" data-min="{ask.get("min") or 1}"'
                 + "".join(f' data-{k}="{html.escape(str(ask[k]))}"' for k in ("lo", "hi", "last", "basis") if ask.get(k) is not None))
        form = ('<form class="pricef"><label>Your price <input type="number" inputmode="numeric" step="1" required> P</label>'
                '<button type="submit">Copy request</button><div class="hint dim small"></div><div class="err small"></div></form>')
    return (f'<button class="trade" data-text="{text}"{attrs}>{label}</button>'
            f'<div class="copied" hidden>{form}<div class="lbl"{" hidden" if ask else ""}>Read it, then run it <span class="ok">copied ✓</span></div>'
            f'<pre{" hidden" if ask else ""}>{text}</pre></div>')


def _choice(c, vid, deadline, sell):
    """(text, label) of the best call this card can offer, in this order: take an offer resting on our market (one call,
    settles next tick), buy or sell at the best price in the Bazaar on our market, or post your own side."""
    want = "buy" if sell else "sell"   # selling means taking a resting bid; buying means taking a resting ask
    resting = next((o for o in c["on_ours"] if o["side"] == want), None)
    if resting:  # the price is the resting offer's: nothing to ask
        return _accept_text(c, resting, vid), ("Sell in 1 click" if sell else "Buy in 1 click"), None
    price = c.get("sell_at") if sell else c.get("buy_at")
    if price:
        return _price_text(c, vid, price, sell), ("Sell in 1 click" if sell else "Buy in 1 click"), _ask(c, price)
    return _post_text(c, vid, sell), ("Sell in 1 click" if sell else "Bid in 1 click"), _ask(c, None)


def _ask(c, price):
    """What the price field needs: the suggestion, the floor and, when the card has traded, its usual range."""
    h = c.get("history")
    out = {"def": price, "min": 1}
    if h:
        out.update(lo=h["usual"][0], hi=h["usual"][1], last=h["last"], basis=h["basis"])
    return out


def _howto(c, vid, deadline, sell):
    return _button(*_choice(c, vid, deadline, sell))


def _side_top(c, sell):
    """(price line, count line) of one side. Several offers: 'from 10 P' and '3 offers, up to 15 P' (for buyers:
    'up to 15 P' and '3 buyers, from 8 P')."""
    price = c.get("sell_at") if sell else c.get("buy_at")
    n = c["buyers"] if sell else c["sellers"]
    if not price:
        return "–", ("no buyer yet" if sell else "no seller yet")
    far = c.get("sell_lo") if sell else c.get("buy_hi")
    fire = "🔥 " if is_deal(c, sell) else ""
    if n > 1 and far is not None and far != price:
        return ((f"{fire}up to {price} P", f"{n} buyers, from {far} P") if sell
                else (f"{fire}from {price} P", f"{n} offers, up to {far} P"))
    word = ("buyer" if sell else "offer") + ("s" if n != 1 else "")
    return f"{fire}{price} P", f"{n} {word}"


DEAL_PCT = 15  # 🔥 when an ask is this much below the card's usual range, or a bid this much above it


def is_deal(c, sell) -> bool:
    """A good deal against what the card usually trades at: buying under the usual range's low end, or selling over
    its high end, by DEAL_PCT percent or more. Needs at least 3 trades behind the range."""
    h, price = c.get("history"), (c.get("sell_at") if sell else c.get("buy_at"))
    if not h or not price or len([x for x in h["recent"] if x[2] == h["basis"]]) < 3:
        return False
    lo, hi = h["usual"]
    return price >= hi * (1 + DEAL_PCT / 100) if sell else price <= lo * (1 - DEAL_PCT / 100)


def _blocked(c, sell):
    """Why a side cannot trade at all, or None. A set not released yet has no copy of any card; a card nobody has
    pulled yet (minted 0, from the live catalog) cannot be sold, though a bid for it is fine: it waits for the first."""
    if not c.get("released", True):
        return "opens with the set"
    if sell and c.get("minted") == 0:
        return "no copy pulled yet"
    return None


def _hist_html(c):
    h = c.get("history")
    return f'<div class="hist small" data-hist="{html.escape(c["ref"])}">{history.spark(h)}<span>{html.escape(history.line(h))}</span></div>'


def live(data: dict) -> dict:
    """The light feed the page polls every tick: per card and side, the price line, the count line and the button."""
    pub = public(data)
    vid = (pub.get("our_venue") or {}).get("venue", "v24")
    out = {}
    for c in pub["cards"]:
        sides = {}
        for sell, key in ((False, "buy"), (True, "sell")):
            text, label, ask = _choice(c, vid, pub["deadline"], sell)
            price, count = _side_top(c, sell)
            sides[key] = {"price": price, "count": count, "label": label, "text": text, "ask": ask}
        sides["hist"] = {"line": history.line(c.get("history")), "spark": history.spark(c.get("history"))}
        out[c["ref"]] = sides
    blocked = sum(1 for c in pub["cards"] if not c.get("hidden") for sell in (False, True) if _blocked(c, sell))
    return {"at": pub["at"], "tick": pub["tick"], "cards": out, "blocked": blocked}


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


def public_vid():
    """Our market's id, from the last build (the auction endpoint needs it)."""
    return _LAST.get("vid")


_LAST = {}


def _auctions_html(data, vid):
    """Lots: the open ones with every bid on our book (offer id, price), the ended ones with their published ranking,
    and the rules in plain words."""
    try:
        import auctions
        lots = list(auctions.load().values())
        grace, rules = auctions.ACCEPT_TICKS, auctions.RULES
    except Exception:
        lots, grace, rules = [], 8, ""
    tick = data.get("tick") or 0
    cards = {c["ref"]: c for c in data["cards"]}
    esc = html.escape

    def card_cell(lot):
        c = cards.get(lot["ref"]) or {"name": lot["ref"]}
        return (f'<td class="cardcell"><div class="thumb" data-card="{esc(lot["ref"])}"></div><div class="cardtxt">'
                f'<b>{esc(lot["ref"])}</b><div>{esc(str(c.get("name") or ""))}</div>'
                f'<div class="dim small">lot {lot["lot"]} · seller {esc(lot["seller"])}</div></div></td>')

    def bid_list(bids, win=None):
        if not bids:
            return '<span class="dim">no bid at or above the reserve</span>'
        return "<br>".join((f'<b>offer {b["id"]} · {b["price"]} P</b> ← winner' if b["id"] == win else f'offer {b["id"]} · {b["price"]} P')
                           for b in bids[:6]) + (f'<br><span class="dim">+{len(bids) - 6} more</span>' if len(bids) > 6 else "")

    rows = []
    for lot in sorted((l for l in lots if l["status"] == "open"), key=lambda l: l["end"]):
        c = cards.get(lot["ref"]) or {}
        bids = sorted(({"id": x["id"], "price": x["price"]} for x in c.get("bids") or [] if x.get("ours") and x["price"] >= lot["reserve"]),
                      key=lambda b: (-b["price"], b["id"]))
        step = max(lot["reserve"], bids[0]["price"] + 1 if bids else lot["reserve"])
        text = (f"Lot {lot['lot']} on {vid}: {lot['ref']} ({c.get('name') or lot['ref']}), reserve {lot['reserve']} P, "
                f"closes at tick {lot['end']}.\n\nYour bid:\n\n"
                + _curl(f'{{"venue":"{vid}","give":{{"cash":YOUR_PRICE}},"want":{{"cards":["{lot["ref"]}"]}}}}') +
                f"\n\nYou pay your own bid if you win. Cancel any time before the close: DELETE /api/offers/OFFER_ID.")
        rows.append(f'<tr>{card_cell(lot)}<td><div class="price">{lot["reserve"]} P</div><div class="dim small">reserve</div></td>'
                    f'<td class="small">{bid_list(bids)}</td>'
                    f'<td><div class="price">{max(0, lot["end"] - tick)}</div><div class="dim small">ticks left · closes at {lot["end"]}</div></td>'
                    f'<td>{_button(text, "Bid in 1 click", {"def": step, "min": lot["reserve"]})}</td></tr>')
    done = []
    for lot in sorted((l for l in lots if l["status"] in ("ended", "closed")), key=lambda l: -l["closed_tick"])[:6]:
        rk, gone = lot.get("ranking") or [], set(lot.get("left_book") or [])
        live = [b for b in rk if b["id"] not in gone]
        win = live[0]["id"] if live else None
        if lot["status"] == "ended" and win:
            state = f"seller: accept offer {win} by tick {lot['accept_by']}"
        elif lot["status"] == "ended":
            state = "no bid left on the book"
        else:
            state = "closed"
        gone_txt = f'<div class="dim small">left the book since the close (accepted or cancelled): {", ".join(map(str, sorted(gone)))}</div>' if gone else ""
        done.append(f'<tr>{card_cell(lot)}<td><div class="price">{lot["reserve"]} P</div><div class="dim small">reserve</div></td>'
                    f'<td class="small">{bid_list(rk, win)}{gone_txt}</td>'
                    f'<td><div class="dim small">closed at tick {lot["closed_tick"]}</div><div class="small">{esc(state)}</div></td><td></td></tr>')
    head = '<thead><tr><th>Lot</th><th>Reserve</th><th>Bids on the book (offer id · price)</th><th>Ends</th><th></th></tr></thead>'
    table = (f'<div class="wrap"><table class="deck lots">{head}<tbody>{"".join(rows)}</tbody></table></div>' if rows
             else '<div class="box dim">No open lot right now. Be the first: auction a card below.</div>')
    results = (f'<h3>Results</h3><div class="wrap"><table class="deck lots">{head}<tbody>{"".join(done)}</tbody></table></div>' if done else "")
    how = (f"Auction a card on {vid}: one call, no ask, the card stays with you until you accept a bid.\n\n"
           f'curl "{PUBLIC}/board/auction?card=CARD_ID&reserve=RESERVE&ticks=30&seller=YOUR_TEAM_ID"\n\n'
           f"CARD_ID like LAT-10; RESERVE = the least you accept; ticks 8 to 60 (30 ticks = 7.5 minutes); "
           f"YOUR_TEAM_ID like t07.\n\nWhen it closes, accept the best bid still on the book within {grace} ticks:\n\n"
           f'curl -X POST $BAZAAR_URL/api/offers/OFFER_ID/accept -H "X-Team-Key: $BAZAAR_KEY" '
           f"-H \"Content-Type: application/json\" -d '{{\"assets\":[YOUR_ASSET_ID]}}'")
    steps = (f'<details class="box rules"><summary><b>How an auction works</b> <span class="dim small">· 5 rules, nothing to trust us for</span></summary><ol>'
             f'<li><b>The seller keeps the card.</b> No ask is posted, so nobody can buy it around the auction.</li>'
             f'<li><b>Bids are ordinary open bids on {vid}</b> (cash for that card, at least the reserve). Everyone sees every bid: '
             f'<code>GET /api/venues/{vid}/offers</code>. Raise with a higher bid, withdraw by cancelling.</li>'
             f'<li><b>At the closing tick we publish the ranking</b> of the bids still on the book, best first, a tie to the earlier offer: '
             f'here and in <a href="/board/lots.json">/board/lots.json</a>. Compare it with the book yourself.</li>'
             f'<li><b>The seller accepts the best bid still on the book</b> within {grace} ticks. The winner pays its own bid, 0% fee.</li>'
             f'<li>If the seller does not accept, nothing trades and every bid stays yours to cancel. We never touch the card or the cash; '
             f'Team 13 cannot bid or sell on its own market.</li></ol></details>')
    return (f'<h2 id="auctions">Auctions <span class="dim" style="font-weight:400;font-size:14px">· on {vid}: open bids, '
            f'the seller accepts the best, you pay your own bid</span></h2>{steps}{table}{results}'
            f'<div style="margin:8px 0 18px">{_button(how, "Auction your card in 1 call")}</div>')


def _lots_link() -> str:
    try:
        import auctions
        n = sum(l.get("status") == "open" for l in auctions.load().values())
    except Exception:
        n = 0
    return f' · <a href="#auctions">🔨 {n} live auction{"s" if n != 1 else ""}</a>' if n else ""


def render(data: dict) -> str:
    pub = public(data)
    ov = pub.get("our_venue") or {}
    vid = html.escape(ov.get("venue", "v24"))
    lots_link = _lots_link()
    _LAST["vid"] = ov.get("venue")
    deadline = pub["deadline"]

    def setnav():
        """Quick links to each set's table, in page order."""
        seen, links = set(), []
        for c in sorted(pub["cards"], key=lambda x: x.get("order") or (99, 99)):
            name, sid = c.get("set") or "?", c.get("set_id") or c.get("set") or "?"
            if name in seen or c.get("hidden"):
                continue
            seen.add(name)
            n = sum(1 for x in pub["cards"] if x.get("set") == name and not x.get("hidden") and (x["buyers"] or x["sellers"]))
            badge = f'<span class="n">{n}</span>' if c.get("released", True) else '<span class="n soon">soon</span>'
            links.append(f'<a href="#set-{html.escape(str(sid))}">{html.escape(name)}{badge}</a>')
        return "".join(links)

    def deck():
        """Every card of the Bazaar, by set, each with what the board knows about it and both calls."""
        by_set, order = {}, []
        for c in sorted(pub["cards"], key=lambda x: x.get("order") or (99, 99)):
            k = c.get("set") or "?"
            if k not in by_set:
                by_set[k], _ = [], order.append(k)
            by_set[k].append(c)
        def side(c, sell):
            price, count = _side_top(c, sell)
            none = " none" if price == "–" else ""
            blocked = _blocked(c, sell)
            if blocked:  # nothing to trade: no copy of this card exists yet
                return (f'<td class="side" data-label="{"Sell it" if sell else "For sale"}">'
                        f'<div class="price none">–</div><div class="dim small">{blocked}</div></td>')
            return (f'<td class="side" data-ref="{html.escape(c["ref"])}" data-side="{"sell" if sell else "buy"}" data-label="{"Sell it" if sell else "For sale"}">'
                    f'<div class="price{none}">{price}</div><div class="dim small count">{count}</div>{_howto(c, vid, deadline, sell)}</td>')

        def row(c):
            quiet = "quiet" if c["state"] == "quiet" else ""
            meta = " · ".join(x for x in (str(c.get("rarity") or "").capitalize(), str(c.get("set") or "")) if x)
            status = ""
            if not c.get("released", True):
                status = '<span class="tag soon">not released yet</span>'
            elif c.get("minted") == 0:
                status = f'<span class="tag none">not pulled yet · 0 of {c.get("print_run") or "?"}</span>'
            elif c.get("minted") is not None:
                status = f'<span class="tag">{c["minted"]} of {c.get("print_run") or "?"} in play</span>'
            return (f'<tr class="{quiet}"><td class="cardcell"><div class="thumb" data-card="{html.escape(c["ref"])}"></div>'
                    f'<div class="cardtxt"><b>{html.escape(c["ref"])}</b><div>{html.escape(str(c.get("name") or ""))}</div>'
                    f'<div class="dim small">{html.escape(meta)}</div><div class="small">{status}</div>{_hist_html(c)}</div></td>'
                    f'{side(c, False)}{side(c, True)}</tr>')

        out = []
        for name in order:
            cards = [c for c in by_set[name] if not c.get("hidden")]
            rows = "".join(row(c) for c in cards)
            empty = " empty" if all(c["state"] == "quiet" for c in cards) else ""
            sid = html.escape(str(cards[0].get("set_id") or name)) if cards else html.escape(name)
            if cards and not cards[0].get("released", True):
                note = "not released yet: no copy exists until the set opens"
            else:
                live_n = sum(1 for c in cards if (c.get("minted") or 0) > 0)
                note = f"{live_n} of {len(cards)} cards in play" + (f", {len(cards) - live_n} not pulled yet" if live_n < len(cards) else "")
            out.append(f'<div class="set{empty}" id="set-{sid}"><h3>{html.escape(name)} <span class="dim small setnote">· {note}</span></h3><div class="wrap"><table class="deck sets"><thead><tr>'
                       f'<th>Card</th><th>For sale <span class="dim">· best price to buy</span></th>'
                       f'<th>Sell it <span class="dim">· best price you get</span></th></tr></thead><tbody>{rows}</tbody></table></div></div>')
        return "".join(out)

    when = time.strftime("%H:%M", time.localtime(pub["at"]))
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>El Club Board</title>
<meta name="description" content="Every card in the Bazaar at its best price. Buy or sell in 1 click, 0% fee, live prices, deals and auctions.">
<meta property="og:type" content="website"><meta property="og:site_name" content="El Club · Team 13">
<meta property="og:title" content="El Club Board: every card at its best price">
<meta property="og:description" content="Buy or sell any card in 1 click. 0% fee, live prices, 🔥 deals, auctions.">
<meta property="og:url" content="{PUBLIC}/board">
<meta property="og:image" content="{PUBLIC}/board/og.jpg"><meta property="og:image:type" content="image/jpeg">
<meta property="og:image:width" content="1200"><meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image"><meta name="theme-color" content="#1b0c22">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Big+Shoulders+Display:wght@600;800&family=Manrope:wght@400;600;800&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/board/cromo.css">
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
h3{{font-size:15px;margin:20px 0 6px;color:var(--gold)}}

.pill.quietpill{{color:var(--dim)}}.hero{{font-size:20px;line-height:1.35;margin:8px 0 6px;max-width:820px}}.hero2{{font-size:15px;margin:0 0 6px;max-width:820px}}
button.toggle{{float:right;font:inherit;font-size:12px;padding:3px 10px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--dim);cursor:pointer}}
td.acts{{white-space:nowrap}}td.acts button.trade{{margin:2px 4px 2px 0}}
:root{{--font-display:"Big Shoulders Display",system-ui,sans-serif;--font-sans:Manrope,system-ui,sans-serif;--color-gold:#e0b45a;--color-muted:#9a958a;--color-base:#1b0c22}}
table.deck{{table-layout:fixed;width:100%;min-width:720px}}table.deck td{{vertical-align:middle;overflow-wrap:anywhere}}table.deck.sets th:nth-child(1){{width:30%}}table.deck.sets th:nth-child(n+2){{width:35%}}table.deck.lots th:nth-child(1){{width:24%}}table.deck.lots th:nth-child(2){{width:11%}}table.deck.lots th:nth-child(3){{width:23%}}table.deck.lots th:nth-child(4){{width:14%}}table.deck.lots th:nth-child(5){{width:28%}}td.cardcell{{display:flex;gap:12px;align-items:center}}
.thumb{{width:80px;height:112px;flex:none}}.thumb .cromo{{font-size:5px}}.thumb:empty{{background:var(--line);border-radius:6px}}
.cardtxt{{min-width:0;overflow-wrap:anywhere}}.cardtxt b{{font-size:15px}}td.side{{min-width:170px}}.price{{font-size:20px;font-weight:700}}.price.none{{color:var(--dim)}}
td.side button.trade{{margin-top:6px}}.pricef{{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-bottom:6px}}.pricef input{{width:90px;font:inherit;padding:4px 6px}}.pricef button{{font:inherit;font-size:13px;padding:4px 10px;border-radius:8px;border:1px solid var(--gold);background:var(--gold);color:#fff;cursor:pointer}}.err{{color:#d9534f;width:100%}}.hint{{width:100%}}.hist{{margin-top:4px;color:var(--dim)}}.sparkwrap{{position:relative;display:block;width:max-content;cursor:zoom-in;outline:none}}.sparkwrap>svg{{display:block;margin-bottom:2px}}.sparkwrap polyline{{stroke:var(--dim);opacity:.55}}
.tag{{color:var(--dim)}}.tag.none{{color:var(--amber)}}.tag.soon{{color:var(--gold);font-weight:600}}.setnav .n.soon{{background:var(--gold);color:#fff}}.setnote{{font-weight:400}}.set{{scroll-margin-top:110px}}#auctions,#how{{scroll-margin-top:110px}}
.sheetx{{display:none}}#backdrop{{display:none}}.sheettitle{{display:none}}.sheet .sheettitle{{display:block;margin:-22px 30px 10px 0;font-size:15px}}.sparkbig{{display:none;position:absolute;left:0;top:24px;z-index:20;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:8px 8px 4px;box-shadow:0 6px 24px rgba(0,0,0,.18);width:276px}}.sparkwrap:hover .sparkbig,.sparkwrap:focus .sparkbig,.sparkwrap:focus-within .sparkbig{{display:block}}@media (max-width:640px){{.hero{{font-size:16px}}table.deck.sets,table.deck.lots{{min-width:0;table-layout:auto}}table.deck.sets thead,table.deck.lots thead{{display:none}}table.deck.sets tr,table.deck.lots tr{{display:grid;grid-template-columns:1fr 1fr;gap:10px 12px;padding:12px;border-bottom:1px solid var(--line)}}table.deck.sets td,table.deck.lots td{{display:block;padding:0;border:0;width:auto}}table.deck td.cardcell{{grid-column:1/-1;display:flex}}table.deck.lots td:last-child{{grid-column:1/-1}}td.side::before{{content:attr(data-label);display:block;font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--dim);margin-bottom:2px}}.pricef input{{font-size:16px}}.copied.sheet{{position:fixed;left:8px;right:8px;top:8px;max-height:calc(100dvh - 16px);overflow:auto;z-index:60;margin:0;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px;box-shadow:0 10px 40px rgba(0,0,0,.35)}}.copied.sheet .sheetx{{display:block;position:sticky;top:0;margin-left:auto;font:inherit;font-size:22px;line-height:1;border:0;background:none;color:var(--ink);padding:0 4px}}#backdrop.on{{display:block;position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:55}}.thumb{{width:60px;height:84px}}.thumb .cromo{{font-size:3.75px}}.sparkbig{{left:-72px}}}}.rules summary,.how summary{{cursor:pointer}}.how{{margin:18px 0}}.how .steps{{margin-top:12px}}.status{{margin:4px 0 10px}}.rules ol{{margin:6px 0 0 18px;padding:0}}.rules li{{margin:3px 0}}
.setnav{{position:sticky;top:0;z-index:5;display:flex;flex-wrap:wrap;gap:6px;padding:8px 0;margin:6px 0 4px;background:var(--bg)}}
.setnav a{{text-decoration:none;color:var(--ink);border:1px solid var(--line);background:var(--card);border-radius:999px;padding:4px 12px;font-size:13px}}
.setnav a:hover{{border-color:var(--gold);color:var(--gold)}}.setnav .n{{color:var(--dim);margin-left:6px;font-size:12px}}
.set{{scroll-margin-top:56px}}.price.flash{{background:color-mix(in srgb,var(--gold) 25%,transparent);border-radius:6px;transition:background 1s}}
table td{{vertical-align:middle}}
button.trade{{margin-top:6px;font:inherit;font-size:13px;padding:5px 12px;border-radius:8px;border:1px solid var(--gold);background:transparent;color:var(--gold);cursor:pointer}}
button.trade:hover{{background:var(--gold);color:#fff}}.copied{{margin-top:8px}}.ok{{color:var(--green);font-weight:600;margin-left:6px}}
</style></head><body><main>
<h1>El Club Board</h1>
<div class="hero">Every card in the Bazaar at its <b>best price</b>. Buy or sell in 1 click. 🔥 = well under the usual price.</div>
<div class="dim small status">All {pub["markets"]} markets · tick <span id="tick">{pub["tick"]}</span> · updated <span id="upd">{when}</span> · live{lots_link} · <a href="#how" class="howlink">How it works</a></div>
<nav class="setnav">{setnav()}</nav>
<div id="deck">{deck()}</div>

{_auctions_html(data, vid)}

<details id="how" class="box how"><summary><b>How it works, and why it is safe</b></summary>
<div class="steps">
<div class="box step"><b class="n">1</b><b>Find your card</b><br><span class="dim"><b>Buy</b> shows the cheapest seller in the Bazaar, <b>Sell</b> the best buyer.</span></div>
<div class="box step"><b class="n">2</b><b>Click and paste</b><br><span class="dim">Set your price; the button copies one ready call. Paste it to your agent, or run it yourself.</span></div>
<div class="box step"><b class="n">3</b><b>Matched on {vid}</b><br><span class="dim">Buyers and sellers from this board meet on {vid}, our market, and are matched the tick both are there.</span></div>
</div>
<p>Not for sale anywhere? <b>Bid on {vid}</b>: we will find this card for you. Selling something nobody bids for? We ask the teams that collect its set.</p>
<p class="dim small">The calls use your kit's <code>$BAZAAR_URL</code> (https://bazaar.causaprima.ai) and <code>$BAZAAR_KEY</code>.
Under each card: its last trade price, the usual range of recent trades, how many and the trend (↑ ↓ →); tap the little chart for a big one.</p>
<ul>
<li><b>We cannot be your counterparty.</b> The rules say a team cannot trade on its own venue with its team key, so
Team 13 is never on the other side of a trade on {vid}. We take the fee, and the fee is zero.</li>
<li><b>The broker is public about what it does.</b> It crosses a bid and an ask at the midpoint between them, matches
the pairs that create the most value first, and never prices a pair worse than the free auto stall would have.</li>
<li><b>Every button copies a complete call</b> to the official API with your own key, with the ids and prices filled
in. Read it before you run it: there is nothing in it but your own offer.</li>
<li><b>Nothing locks you in.</b> An open offer costs nothing while it waits, and you can cancel it at any tick.</li>
<li><b>No team is ever named</b> on this page, in either direction, and no other market's prices are shown.</li>
</ul></details>

<footer class="dim">JSON for your agent: <a href="board.json">board.json</a> · <a href="board/live.json">live.json</a> (every 15 s) · <a href="board/history.json">history.json</a> (trade prices per card) · <a href="board/lots.json">lots.json</a> (auctions) · Team 13</footer>
</main>
<script>
let busyUntil = 0;
// On a phone the price form and the request open as a sheet at the top of the screen: the keyboard comes up from
// below and never covers it, and the table under it does not move.
const backdrop = document.createElement("div"); backdrop.id = "backdrop"; document.body.appendChild(backdrop);
let sheetBox = null;
function openSheet(box, b) {{
  closeSheet(); sheetBox = box; box.classList.add("sheet"); backdrop.classList.add("on");
  if (!box.querySelector(".sheetx")) {{
    const x = document.createElement("button"); x.className = "sheetx"; x.type = "button"; x.textContent = "×"; x.setAttribute("aria-label", "Close");
    x.addEventListener("click", closeSheet); box.prepend(x);
  }}
  let t = box.querySelector(".sheettitle");
  if (!t) {{ t = document.createElement("div"); t.className = "sheettitle"; box.insertBefore(t, box.children[1]); }}
  const td = b.closest("td"), ref = td && (td.dataset.ref || td.closest("tr")?.querySelector("[data-card]")?.dataset.card);
  t.innerHTML = "<b>" + (ref || "") + "</b> · " + b.textContent;
}}
function closeSheet() {{
  if (sheetBox) {{ sheetBox.classList.remove("sheet"); sheetBox.hidden = true; }}
  sheetBox = null; backdrop.classList.remove("on");
}}
backdrop.addEventListener("click", closeSheet);
document.querySelectorAll("a.howlink").forEach(a => a.addEventListener("click", () => {{ document.getElementById("how").open = true; }}));
const PHONE = matchMedia("(max-width:640px)").matches ? "1" : "0";
function ping(e, b, auto) {{
  const q = new URLSearchParams({{e, m: PHONE}});
  if (auto === "1") q.set("auto", "1");
  const td = b && b.closest("td"), side = td && td.dataset.side;
  const ref = td && (td.dataset.ref || td.closest("tr")?.querySelector("[data-card]")?.dataset.card);
  if (ref) q.set("ref", ref);
  if (side) q.set("side", side); else if (b) q.set("side", b.closest("table.lots") ? "lot" : "auction");
  if (b) q.set("l", b.textContent.trim());
  try {{ navigator.sendBeacon("/board/ping?" + q); }} catch (err) {{}}
}}
let autoReload = "0";
try {{ autoReload = sessionStorage.getItem("autoReload") || "0"; sessionStorage.removeItem("autoReload"); }} catch (e) {{}}
ping("view", null, autoReload);
async function copyOut(box, text) {{
  ping("copy", box.previousElementSibling);
  const pre = box.querySelector("pre"), lbl = box.querySelector(".lbl");
  pre.textContent = text; pre.hidden = false; lbl.hidden = false;
  try {{ await navigator.clipboard.writeText(text); lbl.querySelector(".ok").textContent = "copied ✓"; }}
  catch (e) {{ const r = document.createRange(); r.selectNodeContents(pre);
    const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r); lbl.querySelector(".ok").textContent = "selected: press Ctrl/Cmd+C"; }}
}}
// A whole number from min to MAX; anything else is refused with the reason.
function checkPrice(raw, min) {{
  const v = String(raw).trim();
  if (!/^[0-9]+$/.test(v)) return [null, "Enter a whole number of P (digits only)."];
  const n = parseInt(v, 10);
  if (n < min) return [null, min > 1 ? `At least ${{min}} P (the reserve).` : "At least 1 P."];
  if (n > {MAX_PRICE}) return [null, "That is more than any card is worth; check the number."];
  return [n, ""];
}}
document.querySelectorAll("button.trade").forEach(b => b.addEventListener("click", () => {{
  const box = b.nextElementSibling, form = box.querySelector("form.pricef");
  if (PHONE === "1") openSheet(box, b);
  ping("click", b);
  box.hidden = false; busyUntil = Date.now() + 120000;
  if (!b.dataset.min) {{ copyOut(box, b.dataset.text); return; }}
  const inp = form.querySelector("input");
  inp.min = b.dataset.min; inp.max = {MAX_PRICE};
  if (!inp.value && b.dataset.def) inp.value = b.dataset.def;
  box.querySelector("pre").hidden = true; box.querySelector(".lbl").hidden = true;
  const d = b.dataset, hint = form.querySelector(".hint");
  hint.textContent = d.lo ? `usual ${{d.lo === d.hi ? d.lo : d.lo + "–" + d.hi}} P · last ${{d.last}} P${{d.basis === "dealer" ? " (with dealers)" : ""}}` : "no trade of this card yet";
  inp.focus({{preventScroll: true}}); inp.select();
}}));
document.querySelectorAll("form.pricef").forEach(f => f.addEventListener("submit", ev => {{
  ev.preventDefault();
  const box = f.parentElement, b = box.previousElementSibling, err = f.querySelector(".err");
  const [n, why] = checkPrice(f.querySelector("input").value, parseInt(b.dataset.min || "1", 10));
  err.textContent = why;
  if (n === null) return;
  copyOut(box, b.dataset.text.split("YOUR_PRICE").join(String(n)));
}}));
fetch("/board/cards.json").then(r => r.json()).then(cards => {{
  document.querySelectorAll(".thumb[data-card]").forEach(t => {{ const h = cards[t.dataset.card]; if (h) t.innerHTML = h; }});
}}).catch(() => {{}});
async function refresh() {{
  try {{
    const r = await fetch("/board/live.json", {{cache: "no-store"}}); if (!r.ok) return;
    const d = await r.json();
    document.getElementById("tick").textContent = d.tick;
    // a set opened or a card was pulled for the first time: its buttons appear with a fresh page
    if (d.blocked !== undefined && d.blocked !== document.querySelectorAll("td.side:not([data-ref])").length
        && Date.now() > busyUntil && !sheetBox) {{ location.reload(); return; }}
    document.getElementById("upd").textContent = new Date(d.at * 1000).toLocaleTimeString([], {{hour: "2-digit", minute: "2-digit", second: "2-digit"}});
    document.querySelectorAll("[data-hist]").forEach(el => {{
      const h = ((d.cards || {{}})[el.dataset.hist] || {{}}).hist;
      if (h && el.querySelector("span").textContent !== h.line) el.innerHTML = h.spark + "<span>" + h.line.replace(/</g, "&lt;") + "</span>";
    }});
    document.querySelectorAll("td.side[data-ref]").forEach(td => {{
      const v = ((d.cards || {{}})[td.dataset.ref] || {{}})[td.dataset.side]; if (!v) return;
      const pr = td.querySelector(".price"), ct = td.querySelector(".count"), b = td.querySelector("button.trade"), pre = td.querySelector(".copied pre");
      if (pr.textContent !== v.price) {{ pr.textContent = v.price; pr.classList.toggle("none", v.price === "–"); pr.classList.add("flash"); setTimeout(() => pr.classList.remove("flash"), 1200); }}
      ct.textContent = v.count; b.textContent = v.label; b.dataset.text = v.text;
      for (const k of ["lo", "hi", "last", "basis"]) {{ if (v.ask && v.ask[k] != null) b.dataset[k] = v.ask[k]; }}
      if (v.ask) {{ b.dataset.min = v.ask.min || 1; b.dataset.def = v.ask.def || ""; }} else {{ delete b.dataset.min; delete b.dataset.def; }}
      if (pre && td.querySelector(".copied").hidden) pre.textContent = v.text;  // never rewrite a request the user is reading
    }});
  }} catch (e) {{}}
}}
setInterval(refresh, 15000);
setInterval(() => {{ if (Date.now() > busyUntil && !getSelection().toString()) {{
  try {{ sessionStorage.setItem("autoReload", "1"); }} catch (e) {{}}
  location.reload();
}} }}, 600000);
</script></body></html>"""

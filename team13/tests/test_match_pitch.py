"""The fee-blocked-cross announcement names both teams, the card, both prices and both offer ids.

    python3 tests/test_match_pitch.py      (no network)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import market  # noqa: E402
import security  # noqa: E402


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))
    return ok


# El Rastro board: makers are pseudonyms; the feed says m1 is t09 and m2 is t05
board = [
    {"id": 100, "maker": "m1", "give": {"assets": [{"ref": "LAV-02"}]}, "want": {"cash": 3}},
    {"id": 101, "maker": "m2", "give": {"cash": 3}, "want": {"types": ["card:LAV-02"]}},
    {"id": 102, "maker": "m3", "give": {"cash": 2}, "want": {"types": ["card:LAV-02"]}},
]
rows = market.stuck_pairs(board, "t13", 500, 1, {100: "t09", 101: "t05"})
results = [check("pair found with both teams and offer ids", rows and rows[0][1:] == ("LAV-02", 3, 3, "t09", "t05", 100, 101), rows)]

text = market.match_pitch("rastro", *rows[0][1:], venue="v24")
results += [
    check("names the buyer, the seller and both offers", text.startswith("t05:") and "t09's ask" in text
          and "offer 101" in text and "offer 100" in text, text),
    check("fits the 240 characters the server keeps and says 0%", len(text) <= market.ANNOUNCE_MAX and "0%" in text, len(text)),
    check("no injection patterns", not security.detect(text)),
]

anon = market.match_pitch("rastro", "LAV-02", 3, 3, "m1", "m2", 100, 101, venue="v24")
results.append(check("an unnamed side stays generic", anon.startswith("Buyer:") and "covers an ask" in anon, anon))
results.append(check("no pseudonyms in the text", "m1" not in anon and "m2" not in anon, anon))

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)

"""Market invite/nudge hooks must not trip our own security.detect (rivals using the same patterns)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import market
import security

VENUE = "v22"


def sample_invite():
    text = market.PITCH.format(venue=VENUE, fee="0%", brand=market.BRAND)
    text += market.outreach_suffix(VENUE)
    return text[:1200]


def sample_nudge():
    text = market.NUDGE.format(
        ref="LAT-02", where="rastro", role="ask", price=12, other="t05", other_price=14,
        venue=VENUE, brand=market.BRAND,
        how=f"POST /api/offers. Set venue to {VENUE}.",
    )
    return (text + market.outreach_suffix(VENUE))[:1200]


for label, body in [("invite", sample_invite()), ("nudge", sample_nudge())]:
    hits = security.detect(body)
    assert not hits, f"{label} triggered security patterns: {hits}"

assert len(sample_invite()) <= 1200
print("market outreach: injection hooks OK, no self-detection, length OK")

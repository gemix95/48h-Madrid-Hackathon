Cashback: 1 P to each side of every trade between two teams on El Club (v03). Budget 20 P a day, max 2 payouts per team per day.
When a trade settles on v03, our market agent sends each side a direct offer "give 1 P, want nothing" on a free market (we cannot offer on v03 itself), plus a thread message saying how to accept.
The team must accept it (`POST /api/offers/{id}/accept`, no cards needed); it settles next tick and the 1 P leaves our cash only then. No accept, no payment.
Every payout collected is announced on our public board; every 4 P paid we check that our market-making points (`mm_points` in `/api/me`) grew, and pause the promo for the day if not.
Code: `team13/market.py` (`cashback*`). Knobs `cashback_*` in the Strategy tab (Market). Tests: `team13/tests/test_cashback.py`.

"""stall_floor restores any free-stall cross the smart plan skipped (Market Test floor)."""
import sys
sys.path.insert(0, ".")
import smart_broker as sb


def test_stall_floor_adds_skipped_cross():
    book = {"bench_offers": [
        {"id": "b1-0", "want": {"cash": 40}, "give": {"cash": 0}},
        {"id": "b1-1", "want": {"cash": 0}, "give": {"cash": 50}},
        {"id": "b1-2", "want": {"cash": 45}, "give": {"cash": 0}},
        {"id": "b1-3", "want": {"cash": 0}, "give": {"cash": 80}},
    ]}
    # Stall zips lowest ask × highest bid while bid>=ask: b1-0×b1-3 (40≤80), then b1-2×b1-1 (45≤50).
    out = sb.stall_floor(book, [], lambda p: 0)
    pairs = {(s, b) for s, b, _ in out}
    assert pairs == {("b1-0", "b1-3"), ("b1-2", "b1-1")}, out
    # Smart already took the best pair; floor must still restore the second stall cross.
    out2 = sb.stall_floor(book, [("b1-0", "b1-3", 60)], lambda p: 0)
    assert {(s, b) for s, b, _ in out2} == {("b1-0", "b1-3"), ("b1-2", "b1-1")}, out2
    print("stall_floor OK: leftover stall crosses restored")


def test_fee_blocks_thin_pair():
    book = {"bench_offers": [
        {"id": "b1-0", "want": {"cash": 20}, "give": {"cash": 0}},
        {"id": "b1-1", "want": {"cash": 0}, "give": {"cash": 20}},
    ]}
    fee = lambda p: 1  # 1 P flat, like a rounded 1% on small prices
    out = sb.stall_floor(book, [], fee)
    assert out == [], out
    assert sb._price_with_fee(20, 20, fee) is None
    assert sb._price_with_fee(20, 20, lambda p: 0) == 20
    print("fee_block OK: thin pair needs 0% fee")


if __name__ == "__main__":
    test_stall_floor_adds_skipped_cross()
    test_fee_blocks_thin_pair()

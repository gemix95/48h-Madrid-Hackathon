"""Workshop: burn three duplicates of one rarity only when the next card is worth more."""
import sys
sys.path.insert(0, ".")

from values import Values
from workshop import choose, spare_copies

CAT = {
    "values": {"copy_marginals": [1.0, 0.25, 0.1], "page_bonus": 0.25, "master_bonus": 0.1},
    "sets": [
        {"id": "LAT", "released": True, "cards": [
            {"id": "LAT-01", "book": 10, "rarity": "common", "page": False},
            {"id": "LAT-02", "book": 10, "rarity": "common", "page": False},
            {"id": "LAT-03", "book": 10, "rarity": "common", "page": False},
            {"id": "LAT-06", "book": 25, "rarity": "uncommon", "page": False},
        ]},
        {"id": "SAL", "released": True, "cards": [
            {"id": "SAL-01", "book": 10, "rarity": "common", "page": False},
            {"id": "SAL-02", "book": 10, "rarity": "common", "page": False},
            {"id": "SAL-03", "book": 10, "rarity": "common", "page": False},
            {"id": "SAL-06", "book": 25, "rarity": "uncommon", "page": False},
            {"id": "SAL-09", "book": 70, "rarity": "rare", "page": False},
        ]},
    ],
}
AFF = {"LAT": 0.5, "SAL": 1.6}


def hand(holdings):
    assets, n = [], 1
    for ref, count in holdings.items():
        for i in range(count):
            assets.append({"id": n, "kind": "card", "ref": ref, "serial": i + 1})
            n += 1
    return Values(CAT, {"affinity": AFF, "assets": assets})


def test_crafts_cheap_duplicates():
    # three Latina second copies are worth 1.25 each; a missing Salamanca uncommon is worth 40
    v = hand({"LAT-01": 2, "LAT-02": 2, "LAT-03": 2})
    plan = choose(v, set(), edge=2)
    assert plan is not None
    assert plan["rarity"] == "common" and plan["next"] == "uncommon"
    assert sorted(plan["refs"]) == ["LAT-01", "LAT-02", "LAT-03"]
    assert plan["surplus"] > 2
    print("cheap duplicates craft", plan["loss"], "->", plan["ev"])


def test_skips_when_the_spares_are_worth_more():
    # three Salamanca second copies cost 4 each; the next uncommons would only be second copies
    v = hand({"SAL-01": 2, "SAL-02": 2, "SAL-03": 2, "LAT-06": 1, "SAL-06": 1})
    assert choose(v, set(), edge=2) is None
    print("expensive duplicates stay")


def test_keeps_the_last_copy_and_skips_locked():
    v = hand({"LAT-01": 1, "LAT-02": 1, "LAT-03": 1, "SAL-01": 2})
    assert spare_copies(v, set()) == [] or all(a["ref"] == "SAL-01" for a in spare_copies(v, set()))
    assert choose(v, set(), edge=0) is None  # only one duplicate ref
    v2 = hand({"LAT-01": 2, "LAT-02": 2, "LAT-03": 2})
    locked = {a["id"] for a in v2.assets if a["ref"] == "LAT-01" and a["serial"] == 2}
    assert choose(v2, locked, edge=2) is None  # one of the three fuels is listed
    # two free copies of LAT-01 still leave one spare, plus the other two refs
    v3 = hand({"LAT-01": 3, "LAT-02": 2, "LAT-03": 2})
    locked3 = {a["id"] for a in v3.assets if a["ref"] == "LAT-01" and a["serial"] == 3}
    plan = choose(v3, locked3, edge=2)
    assert plan is not None and "LAT-01" in plan["refs"]
    print("last copy and listed copy stay")


def test_prefers_the_bigger_upgrade():
    # Three cheap common duplicates, and three uncommon duplicates. The missing rare
    # (book 70 × 1.6) beats a random uncommon we already own, so the uncommon trio goes first.
    v = hand({"LAT-01": 2, "LAT-02": 2, "LAT-03": 2, "LAT-06": 2, "SAL-06": 3})
    plan = choose(v, set(), edge=2)
    assert plan["rarity"] == "uncommon" and plan["next"] == "rare"
    assert plan["surplus"] > 20
    print("bigger upgrade first", plan["rarity"], plan["surplus"])


if __name__ == "__main__":
    test_crafts_cheap_duplicates()
    test_skips_when_the_spares_are_worth_more()
    test_keeps_the_last_copy_and_skips_locked()
    test_prefers_the_bigger_upgrade()
    print("workshop ok")

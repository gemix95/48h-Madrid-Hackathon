# El Consejo: shared board of Team 13's agents

Written only by `team13/council.py` (agents, tuner, `council.py post`). One file per writer in `notes/`
(`<agent id or tuner/admin>@<host>.jsonl`), so pushes from several laptops never conflict.
Every process pulls this branch every 15 s and pushes each note at once. Read it with `python3 team13/council.py read`.

"""Turn a Claude Code session into plain text you can summarise: your messages and Claude's replies, no tool output,
secrets masked, split into parts small enough for one agent each.

Claude Code keeps every session as JSONL under ~/.claude/projects/<the project folder, slashes as dashes>/<id>.jsonl.

    python3 tools/claude_session_extract.py                     # newest session of this folder, 4 parts
    python3 tools/claude_session_extract.py --session <file.jsonl> --parts 6 --out /tmp/session

Then ask Claude Code, one agent per part: "Read <part>. In English, extract decisions, ideas tried with their outcome,
findings about the game, fair-play questions, incidents and fixes, numbers, lessons. Only facts in the text."
and merge the notes into one Markdown file.
"""
import argparse
import json
import re
from pathlib import Path

SECRET = re.compile(r"(tk-|bk_|sk-ant-|ghp_|github_pat_)[A-Za-z0-9_-]{4,}")


def newest_session() -> Path:
    folder = Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(Path.cwd()))
    files = sorted(folder.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    if not files:
        raise SystemExit(f"no session in {folder}; pass --session")
    return files[-1]


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        if any(b.get("type") == "tool_result" for b in content if isinstance(b, dict)):
            return ""
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", type=Path)
    ap.add_argument("--parts", type=int, default=4)
    ap.add_argument("--out", type=Path, default=Path("session_export"))
    a = ap.parse_args()
    src = a.session or newest_session()
    turns = []
    for line in src.open():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("type") not in ("user", "assistant"):
            continue
        t = text_of((e.get("message") or {}).get("content")).strip()
        if t:
            t = SECRET.sub(r"\1***", t)[:6000]
            turns.append(f"### {e['type'].upper()} {(e.get('timestamp') or '')[:16]} UTC\n{t}\n")
    a.out.mkdir(parents=True, exist_ok=True)
    size = sum(map(len, turns)) // a.parts + 1
    part, cur, n = [], 0, 1
    for t in turns:
        part.append(t)
        cur += len(t)
        if cur >= size and n < a.parts:
            (a.out / f"part{n}.md").write_text("\n".join(part))
            part, cur, n = [], 0, n + 1
    (a.out / f"part{n}.md").write_text("\n".join(part))
    print(f"{src.name}: {len(turns)} turns -> {n} parts in {a.out}/")


if __name__ == "__main__":
    main()

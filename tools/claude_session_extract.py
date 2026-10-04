#!/usr/bin/env python3
"""Strip a Claude Code session JSONL to user/assistant text, mask secrets, split into parts.

Usage:
  python3 tools/claude_session_extract.py [--session PATH] [--parts N] [--out DIR]

Default session: newest/largest *.jsonl under ~/.claude/projects/*/
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

SECRET_PATTERNS = [
    (re.compile(r"(?i)(api[_-]?key|token|password|secret|authorization)\s*[:=]\s*['\"]?[^\s'\"]{8,}"),
     r"\1=[REDACTED]"),
    (re.compile(r"(?i)(sk-[a-zA-Z0-9_-]{20,})"), "[REDACTED_KEY]"),
    (re.compile(r"(?i)(ghp_[a-zA-Z0-9]{20,})"), "[REDACTED_TOKEN]"),
    (re.compile(r"(?i)(xox[baprs]-[a-zA-Z0-9-]{20,})"), "[REDACTED_TOKEN]"),
    (re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._\-+=/]{20,}"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(ANTHROPIC_API_KEY=)[^\s'\"]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(BAZAAR[_A-Z]*KEY=)[^\s'\"]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(DASHBOARD_REMOTE_PASSWORD=)[^\s'\"]+"), r"\1[REDACTED]"),
    (re.compile(r"\b(tk-|bk_|bk-|sk-ant-)[A-Za-z0-9_-]{6,}"), r"\1[REDACTED]"),  # bare game/broker/Anthropic keys
]


def find_default_session() -> Path:
    root = Path.home() / ".claude" / "projects"
    if not root.is_dir():
        raise SystemExit(f"No Claude projects dir at {root}")
    candidates = list(root.glob("*/*.jsonl"))
    if not candidates:
        raise SystemExit(f"No session JSONL under {root}")
    # Prefer newest among the largest (main sessions are usually both)
    candidates.sort(key=lambda p: (p.stat().st_size, p.stat().st_mtime), reverse=True)
    return candidates[0]


def mask(text: str) -> str:
    out = text
    for pat, repl in SECRET_PATTERNS:
        out = pat.sub(repl, out)
    return out


def block_text(block) -> str | None:
    if not isinstance(block, dict):
        return None
    t = block.get("type")
    if t == "text":
        return block.get("text") or ""
    if t == "thinking":
        return None  # omit chain-of-thought
    if t == "tool_use":
        name = block.get("name") or "?"
        inp = block.get("input")
        try:
            payload = json.dumps(inp, ensure_ascii=False, default=str)
        except TypeError:
            payload = str(inp)
        if len(payload) > 1200:
            payload = payload[:1200] + "…"
        return f"[tool_use {name}] {payload}"
    if t == "tool_result":
        content = block.get("content")
        if isinstance(content, str):
            body = content
        elif isinstance(content, list):
            parts = []
            for x in content:
                if isinstance(x, dict) and x.get("type") == "text":
                    parts.append(x.get("text") or "")
                else:
                    parts.append(str(x)[:500])
            body = "\n".join(parts)
        else:
            body = str(content)
        if len(body) > 2500:
            body = body[:2500] + "…"
        return f"[tool_result] {body}"
    return None


def message_text(msg) -> str:
    if not isinstance(msg, dict):
        return ""
    content = msg.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for b in content:
            s = block_text(b)
            if s:
                parts.append(s)
        return "\n".join(parts).strip()
    return ""


def extract_turns(path: Path) -> list[tuple[str, str]]:
    turns = []
    for line in path.open():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = obj.get("type")
        if kind not in ("user", "assistant"):
            continue
        text = message_text(obj.get("message") or {})
        if not text:
            continue
        # Drop pure tool-result user lines that add no prose (keep short ones)
        role = "User" if kind == "user" else "Claude"
        turns.append((role, mask(text)))
    return turns


def split_parts(turns: list[tuple[str, str]], n: int) -> list[list[tuple[str, str]]]:
    if n < 1:
        raise SystemExit("--parts must be >= 1")
    if not turns:
        return [[] for _ in range(n)]
    # Balance by character weight
    weights = [len(t) for _, t in turns]
    total = sum(weights) or 1
    target = total / n
    parts: list[list[tuple[str, str]]] = [[] for _ in range(n)]
    acc = 0
    idx = 0
    for i, turn in enumerate(turns):
        parts[idx].append(turn)
        acc += weights[i]
        if idx < n - 1 and acc >= target * (idx + 1):
            idx += 1
    return parts


def write_parts(parts: list[list[tuple[str, str]]], out_dir: Path, session: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = out_dir / "README.txt"
    meta.write_text(
        f"Source session: {session}\n"
        f"Parts: {len(parts)}\n"
        f"Turns: {sum(len(p) for p in parts)}\n",
        encoding="utf-8",
    )
    for i, part in enumerate(parts, 1):
        path = out_dir / f"part-{i}.md"
        lines = [f"# Session part {i}/{len(parts)}", "", f"_Source: `{session}`_", ""]
        for role, text in part:
            lines.append(f"## {role}")
            lines.append("")
            lines.append(text)
            lines.append("")
        path.write_text("\n".join(lines), encoding="utf-8")
        print(f"wrote {path} ({len(part)} turns, {path.stat().st_size} bytes)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--session", type=Path, help="Path to session JSONL")
    ap.add_argument("--parts", type=int, default=4)
    ap.add_argument("--out", type=Path, default=Path("reports/session_parts"))
    args = ap.parse_args(argv)

    session = args.session or find_default_session()
    if not session.is_file():
        raise SystemExit(f"Session not found: {session}")
    print(f"session: {session} ({session.stat().st_size} bytes)", file=sys.stderr)

    turns = extract_turns(session)
    print(f"extracted {len(turns)} turns", file=sys.stderr)
    parts = split_parts(turns, args.parts)
    write_parts(parts, args.out, session)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

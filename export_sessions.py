#!/usr/bin/env python3
"""Extract the compacted session summaries from a transcript into docs/sessions/.

Long working sessions get compacted when they outgrow the context window: the
conversation so far is replaced by a written summary, which is then the only
surviving record of how a conclusion was reached. Those summaries are the closest
thing this project has to a lab notebook, and they live outside the repository, so
a clone loses them.

This pulls them out rather than pasting them in by hand, so the extraction is
repeatable when a later session compacts again. The emitted files are a starting
point, not the finished article - they carry a lot of harness bookkeeping, and are
edited down afterwards. Re-running will not clobber that editing: existing files
are left alone unless --force is given.

    venv/bin/python export_sessions.py                 # find the transcript
    venv/bin/python export_sessions.py path/to.jsonl   # or name it
"""
import argparse
import glob
import json
import os
import sys

DEFAULT_GLOB = "~/.claude/projects/*Ducks*/*.jsonl"
OUT_DIR = "docs/sessions"

HEADER = """# Session {n}, {date}

A condensed log of one working session, written for continuity rather than as a
designed document. Current conclusions live in [`../notes/`](../notes/); where
these disagree, the notes win.

"""


def summaries(path):
    """Every compaction summary in the transcript, oldest first."""
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue                    # a partially written trailing line
            if not rec.get("isCompactSummary"):
                continue
            content = rec.get("message", {}).get("content")
            if isinstance(content, list):
                text = "".join(part.get("text", "") for part in content
                               if isinstance(part, dict))
            else:
                text = str(content or "")
            if text.strip():
                out.append((rec.get("timestamp", "")[:10], text.strip()))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("transcript", nargs="?",
                    help=f"session transcript; default: newest {DEFAULT_GLOB}")
    ap.add_argument("--out", default=OUT_DIR, help="output directory")
    ap.add_argument("--force", action="store_true",
                    help="overwrite files that already exist, discarding any "
                         "editing done to them")
    args = ap.parse_args()

    path = args.transcript
    if path is None:
        found = sorted(glob.glob(os.path.expanduser(DEFAULT_GLOB)),
                       key=os.path.getmtime)
        if not found:
            print(f"no transcript found at {DEFAULT_GLOB}; name one explicitly")
            return 1
        path = found[-1]
        print(f"transcript: {path}")

    found = summaries(path)
    if not found:
        print("no compaction summaries in that transcript")
        return 1

    os.makedirs(args.out, exist_ok=True)
    for i, (date, text) in enumerate(found, 1):
        dest = os.path.join(args.out, f"{i:02d}-{date}.md")
        if os.path.exists(dest) and not args.force:
            print(f"  kept   {dest} (already exists; --force to replace)")
            continue
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(HEADER.format(n=i, date=date))
            fh.write(text.rstrip() + "\n")
        print(f"  wrote  {dest} ({len(text)} chars)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Verify doc/ guides stay wired into the two bootstrap entry points.

Every *.md under doc/ must be:
  1. @import-ed in CLAUDE.md     (Claude Code auto-bootstrap)
  2. listed in CLAUDE_BOOTSTRAP.md §0  (Claude.ai project / human bootstrap)

Exits non-zero with an actionable message on any mismatch so the gap
surfaces in CI / pre-commit instead of silently breaking auto-load.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC_DIR = ROOT / "doc"
CLAUDE_MD = ROOT / "CLAUDE.md"
BOOTSTRAP_MD = ROOT / "CLAUDE_BOOTSTRAP.md"


def actual_doc_files() -> set[str]:
    return {p.relative_to(ROOT).as_posix() for p in DOC_DIR.rglob("*.md")}


def claude_imports() -> set[str]:
    text = CLAUDE_MD.read_text(encoding="utf-8")
    return {m.group(1) for m in re.finditer(r"^@(\S+)", text, re.MULTILINE)
            if m.group(1).startswith("doc/")}


def bootstrap_refs() -> set[str]:
    text = BOOTSTRAP_MD.read_text(encoding="utf-8")
    return set(re.findall(r"`(doc/[^`\s]+\.md)`", text))


def report(label: str, missing: set[str], stale: set[str]) -> bool:
    ok = not (missing or stale)
    if ok:
        print(f"  OK  {label}")
        return True
    print(f"FAIL  {label}")
    for f in sorted(missing):
        print(f"        누락(파일은 있는데 미등록): {f}")
    for f in sorted(stale):
        print(f"        잔재(등록됐는데 파일 없음): {f}")
    return False


def main() -> int:
    docs = actual_doc_files()
    if not docs:
        print(f"FAIL  doc/ 에 .md 파일이 없음: {DOC_DIR}")
        return 1

    imports = claude_imports()
    refs = bootstrap_refs()

    ok_claude = report(
        "CLAUDE.md @import ↔ doc/",
        missing=docs - imports,
        stale=imports - docs,
    )
    ok_bootstrap = report(
        "CLAUDE_BOOTSTRAP.md §0 ↔ doc/",
        missing=docs - refs,
        stale=refs - docs,
    )

    if ok_claude and ok_bootstrap:
        print(f"\ndoc/ {len(docs)}개 파일이 두 부트스트랩 진입점에 모두 연결됨.")
        return 0

    print(
        "\n갱신 필요: doc/ 변경 시 CLAUDE.md의 @import 목록과 "
        "CLAUDE_BOOTSTRAP.md §0 목록을 함께 맞춰야 자동 흡수가 유지됨 "
        "(doc/skill-define.md §7)."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())

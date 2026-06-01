#!/usr/bin/env python3
"""Verify Tier 0/1 doc bootstrap stays coherent.

Tier 0 (auto-load by CLAUDE.md @import / BOOTSTRAP §0):
  - CLAUDE.md @import must reference exactly {doc/INDEX.md}
  - CLAUDE_BOOTSTRAP.md must reference `doc/INDEX.md`

Tier 1 (lazy-load via INDEX trigger):
  - Every other *.md under doc/ (except README.md, which is a GitHub-UI
    directory guide, and INDEX.md itself) must be backtick-referenced
    from doc/INDEX.md so the trigger catalog stays complete.

Exits non-zero with an actionable message on any mismatch so drift
surfaces in CI / pre-commit instead of silently breaking lazy-load.

See doc/skill-define.md §1 (Tier 0/1 변경 발동 조건) and §7 (자기검증).
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC_DIR = ROOT / "doc"
INDEX_MD = DOC_DIR / "INDEX.md"
CLAUDE_MD = ROOT / "CLAUDE.md"
BOOTSTRAP_MD = ROOT / "CLAUDE_BOOTSTRAP.md"

CORE = "doc/INDEX.md"
# README는 GitHub UI용 디렉토리 가이드 — INDEX 카탈로그 대상 외
CATALOG_EXEMPT = {CORE, "doc/README.md"}


def actual_doc_files() -> set[str]:
    return {p.relative_to(ROOT).as_posix() for p in DOC_DIR.rglob("*.md")}


def claude_imports() -> set[str]:
    text = CLAUDE_MD.read_text(encoding="utf-8")
    return {m.group(1) for m in re.finditer(r"^@(\S+)", text, re.MULTILINE)
            if m.group(1).startswith("doc/")}


def bootstrap_refs() -> set[str]:
    text = BOOTSTRAP_MD.read_text(encoding="utf-8")
    return set(re.findall(r"`(doc/[^`\s]+\.md)`", text))


def index_refs() -> set[str]:
    text = INDEX_MD.read_text(encoding="utf-8")
    return set(re.findall(r"`(doc/[^`\s]+\.md)`", text))


def main() -> int:
    docs = actual_doc_files()
    if not docs:
        print(f"FAIL  doc/ 에 .md 파일이 없음: {DOC_DIR}")
        return 1
    if not INDEX_MD.exists():
        print(f"FAIL  Tier 0 카탈로그 누락: {INDEX_MD.relative_to(ROOT)}")
        return 1

    imports = claude_imports()
    bs_refs = bootstrap_refs()
    idx_refs = index_refs()

    failures: list[str] = []

    if imports != {CORE}:
        failures.append(
            f"CLAUDE.md @import는 정확히 {{{CORE}}}만 포함해야 함. 실제: "
            + (", ".join(sorted(imports)) if imports else "(없음)")
        )

    if CORE not in bs_refs:
        failures.append(
            f"CLAUDE_BOOTSTRAP.md에 `{CORE}` 인용 필수 (Tier 0 surface 양립)"
        )

    expected_catalog = docs - CATALOG_EXEMPT
    missing_from_index = expected_catalog - idx_refs
    if missing_from_index:
        failures.append(
            "doc/INDEX.md가 다음 본문을 카탈로그하지 않음 (Tier 1 lazy-load 누락): "
            + ", ".join(sorted(missing_from_index))
        )

    stale_in_index = idx_refs - docs
    if stale_in_index:
        failures.append(
            "doc/INDEX.md가 가리키는 다음 파일이 실제로 없음 (잔재): "
            + ", ".join(sorted(stale_in_index))
        )

    if failures:
        for f in failures:
            print(f"FAIL  {f}")
        print(
            "\n갱신 필요: doc/ 변경 시 CLAUDE.md @import / "
            "CLAUDE_BOOTSTRAP.md / doc/INDEX.md 카탈로그를 함께 맞춰야 "
            "Tier 0/1 자동 흡수가 유지됨 (doc/skill-define.md §1, §7)."
        )
        return 1

    body_count = len(expected_catalog)
    print(f"  OK  CLAUDE.md @import = {{{CORE}}}")
    print(f"  OK  CLAUDE_BOOTSTRAP.md references {CORE}")
    print(f"  OK  doc/INDEX.md catalogs {body_count} body file(s) "
          f"(README/INDEX 제외)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

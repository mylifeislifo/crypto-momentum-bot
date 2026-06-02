#!/usr/bin/env python3
"""Verify doc/ guides stay wired into the bootstrap entry points and that
cross-references don't rot.

Checks:
  1. Every *.md under doc/ is @import-ed in CLAUDE.md     (Claude Code auto-bootstrap)
  2. Every *.md under doc/ is listed in CLAUDE_BOOTSTRAP.md §0  (Claude.ai / human)
  3. Priority(우선순위) blocks: the `skills/*.md ← …` line must be either the
     generic form (no enumeration) OR enumerate exactly doc/skills/*.md. A
     partial/stale enumeration (the drift class) fails.
  4. Dead §-reference lint: filename-qualified citations `<file> §N[.N]` across
     all docs must point at an existing `##`/`###` heading section.

Exits non-zero with an actionable message on any mismatch so the gap
surfaces in CI / pre-commit instead of silently breaking auto-load.

Check 4 scope / known limitations (kept narrow for ZERO false positives):
  - Only filename-qualified `<file> §N[.N]` is verified. Bare intra-file refs
    (`§2.4`, `§7`) have no filename token and are skipped.
  - List-item refs `… §N.N #M` (e.g. `bot-ops §2.1 #6`): only the base section
    `§N.N` is validated; the `#M` item index is NOT verified.
  - `§N.N.N` deep nesting is out of scope (none currently exist).
  - A citation whose filename token cannot be resolved to a real doc file is
    SKIPPED (not failed) — we only fail on a real file + genuinely absent section.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC_DIR = ROOT / "doc"
CLAUDE_MD = ROOT / "CLAUDE.md"
BOOTSTRAP_MD = ROOT / "CLAUDE_BOOTSTRAP.md"
DOC_README = DOC_DIR / "README.md"
SKILL_DEFINE = DOC_DIR / "skill-define.md"

# 우선순위 블록의 `skills/*.md ← …` 라인
SKILLS_LINE = re.compile(r"^\s*skills/\*\.md\s+←\s*(.+?)\s*$")
# kebab-case 토큰 (스킬 basename 형태)
TOKEN = re.compile(r"[a-z][a-z0-9-]+")
# 파일명 한정 § 인용: <file> §N[.N](·§?N[.N])*  — file 토큰은 ASCII만(한글 앞단어 배제)
CITATION = re.compile(
    r"(?P<file>[A-Za-z0-9_][A-Za-z0-9/_.-]*?)\s+§\s*"
    r"(?P<secs>\d+(?:\.\d+)?(?:·§?\d+(?:\.\d+)?)*)"
)
# 번호 있는 ##/### 헤딩에서 섹션번호(N 또는 N.N) 추출.
# 최상위는 `## N. 제목`(마침표), 하위는 `### N.N 제목`(마침표 없음) → 마침표 선택 허용.
HEADING = re.compile(r"^#{2,3}\s+(\d+(?:\.\d+)?)\.?(?:\s|$)")


def actual_doc_files() -> set[str]:
    return {p.relative_to(ROOT).as_posix() for p in DOC_DIR.rglob("*.md")}


def claude_imports() -> set[str]:
    text = CLAUDE_MD.read_text(encoding="utf-8")
    return {m.group(1) for m in re.finditer(r"^@(\S+)", text, re.MULTILINE)
            if m.group(1).startswith("doc/")}


def bootstrap_refs() -> set[str]:
    text = BOOTSTRAP_MD.read_text(encoding="utf-8")
    return set(re.findall(r"`(doc/[^`\s]+\.md)`", text))


def skill_basenames() -> set[str]:
    return {p.stem for p in (DOC_DIR / "skills").glob("*.md")}


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


def report_dead(label: str, dead: list[str]) -> bool:
    if not dead:
        print(f"  OK  {label}")
        return True
    print(f"FAIL  {label}")
    for row in dead:
        print(f"        끊긴 인용: {row}")
    return False


def parse_priority_rhs(rhs: str, skills: set[str]) -> set[str] | None:
    """우선순위 라인 RHS가 스킬을 열거하면 그 집합, 일반형이면 None.

    '열거 여부'를 '알려진 스킬과의 교집합 비어있지 않음'으로 판정 → 한글/일반
    문구는 ASCII 토큰이 없거나 스킬명이 아니므로 절대 열거로 오인되지 않는다.
    """
    enumerated = set(TOKEN.findall(rhs)) & skills
    return enumerated or None


def check_priority_block(skills: set[str]) -> bool:
    missing: set[str] = set()
    stale: set[str] = set()
    for path in (CLAUDE_MD, DOC_README, SKILL_DEFINE):
        for line in path.read_text(encoding="utf-8").splitlines():
            m = SKILLS_LINE.match(line)
            if not m:
                continue
            enumerated = parse_priority_rhs(m.group(1), skills)
            if enumerated is None:
                continue  # 일반형 — 통과
            rel = path.relative_to(ROOT).as_posix()
            missing |= {f"{rel}: {s}" for s in (skills - enumerated)}
            stale |= {f"{rel}: {s}" for s in (enumerated - skills)}
    return report("우선순위 블록 skills/*.md 열거 ↔ doc/skills/", missing, stale)


def build_resolver() -> dict[str, Path]:
    """인용의 파일명 토큰 → 실제 경로. 여러 alias 등록 후 모호 키 제거."""
    table: dict[str, Path] = {}
    for p in DOC_DIR.rglob("*.md"):
        parts = p.relative_to(DOC_DIR).parts
        table[p.relative_to(DOC_DIR).as_posix()] = p  # prefix형: skills/bot-ops.md
        table[p.name] = p                              # bare: bot-ops.md
        table[p.stem] = p                              # short: bot-ops
        if parts[0] == "domains" and p.name == "rules.md":
            table[parts[1]] = p                        # trading
            table[f"{parts[1]}/rules.md"] = p          # trading/rules.md
    for ambiguous in ("rules.md", "rules"):            # 3개 도메인 충돌 → 제거
        table.pop(ambiguous, None)
    return table


def resolve(token: str, table: dict[str, Path]) -> Path | None:
    return table.get(token.removeprefix("doc/"))


def heading_sections(path: Path) -> set[str]:
    return {m.group(1) for line in path.read_text(encoding="utf-8").splitlines()
            if (m := HEADING.match(line))}


def check_dead_refs() -> bool:
    table = build_resolver()
    sections_cache: dict[Path, set[str]] = {}
    dead: list[str] = []
    sources = list(DOC_DIR.rglob("*.md")) + [CLAUDE_MD, BOOTSTRAP_MD]
    for src in sources:
        rel = src.relative_to(ROOT).as_posix()
        for lineno, line in enumerate(src.read_text(encoding="utf-8").splitlines(), 1):
            for m in CITATION.finditer(line):
                target = resolve(m.group("file"), table)
                if target is None:
                    continue  # 해석 불가 토큰 → skip(precision 우선)
                secs = sections_cache.setdefault(target, heading_sections(target))
                for raw in m.group("secs").split("·"):
                    num = raw.lstrip("§").strip()
                    if num not in secs:
                        dead.append(
                            f"{rel}:{lineno}  «{m.group('file')} §{num}» "
                            f"→ {target.relative_to(ROOT).as_posix()} 에 해당 섹션 없음"
                        )
    return report_dead("문서 간 § 상호참조 (dead-link)", dead)


def main() -> int:
    docs = actual_doc_files()
    if not docs:
        print(f"FAIL  doc/ 에 .md 파일이 없음: {DOC_DIR}")
        return 1

    imports = claude_imports()
    refs = bootstrap_refs()
    skills = skill_basenames()

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
    ok_priority = check_priority_block(skills)
    ok_deadrefs = check_dead_refs()

    if ok_claude and ok_bootstrap and ok_priority and ok_deadrefs:
        print(f"\ndoc/ {len(docs)}개 파일 + 우선순위/상호참조 정합성 모두 통과.")
        return 0

    print(
        "\n갱신 필요: doc/ 변경 시 CLAUDE.md의 @import 목록·"
        "CLAUDE_BOOTSTRAP.md §0 목록·우선순위 블록·§ 상호참조를 함께 맞춰야 함 "
        "(doc/skill-define.md §7)."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())

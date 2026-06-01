# CLAUDE.md

이 레포의 모든 작업은 `doc/INDEX.md`(자동 흡수) → **트리거 매칭되는 본문만 lazy Read** 순서로 시작한다.

## 핵심 룰 5종 (위반 즉시 사고 — 항상 노출)

1. **수치 타입**: 가격·수량·잔고는 `decimal.Decimal`만. `float` 금지. (`doc/domains/trading/rules.md §1.2`)
2. **레버리지 한계**: 2x 절대 초과 금지. (`doc/domains/trading/rules.md §1.1`)
3. **paper 게이트**: backtest → walkforward → paper 7일 → 시드 10%. (`doc/domains/trading/rules.md §1.3`, `doc/skills/progressive-gate.md`)
4. **시크릿 평문 금지**: API key·token·password 채팅·로그·커밋 평문 노출 금지. (`doc/domains/security/rules.md §1.1`)
5. **봇 발언 신뢰성 0**: "완료했다" 발언은 즉시 신뢰 금지. CLI/`get_file_contents`로 직접 검증. SSOT는 `doc/skills/bot-ops.md §2.2`

## 우선순위 (충돌 시)

```
domains/security/rules.md   ← 최상위 (보안은 어떤 룰도 우회 불가)
domains/<other>/rules.md    ← trading, automation
skills/*.md                 ← bot-ops, system-health, infra-debug, progressive-gate, audit-log, signal-validation, blog-automation
skill-define.md             ← 메타 (doc/ 자체 변경 시)
INDEX.md                    ← 카탈로그 (포인터, 본문 우선순위 체인 외)
```

## 자동 흡수 (Tier 0)

@doc/INDEX.md

위 INDEX 1개만 자동 흡수한다. 본문 룰은 INDEX의 트리거 표(§3·§4·§5)에서 작업과 매칭되는 항목을 즉시 Read로 흡수한다. 매칭 없는 새 영역이면 `doc/skill-define.md`도 함께 Read.

## 문서 변경 시

`doc/skill-define.md` 우선 흡수.

- **경량 변경** (오타·예외 누적 추가·인용 보강) → §7 자기검증만
- **중량 변경** (섹션·도메인·파일 신설·삭제·이름변경 / Tier 0/1 자동 흡수 구조 변경) → 5점 체크 → Spec 7항목 사용자 승인 → 본문 → 자기검증

## 검증 원칙 (모든 작업 공통)

- 봇·디스패치·자기 자신의 "완료했다" 발언은 신뢰 금지
- 직접 검증 = `ls -la` / `cat` / `grep` / `ps` / `curl` / `get_file_contents`
- 자기검증 4항목은 `doc/skill-define.md §7`

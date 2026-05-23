# CLAUDE.md

이 레포의 모든 작업은 `doc/` 운영 매뉴얼을 우선 흡수한 뒤 시작한다.

## 우선순위 (충돌 시)

```
domains/security/rules.md   ← 최상위 (보안은 어떤 룰도 우회 불가)
domains/<other>/rules.md    ← trading, automation
skills/*.md                 ← bot-ops, system-health, infra-debug, progressive-gate, audit-log
skill-define.md             ← 메타 (doc/ 자체 변경 시)
```

## 즉시 적용 핵심 룰 5종

1. **수치 타입**: 가격·수량·잔고는 `decimal.Decimal`만. `float` 금지. (`doc/domains/trading/rules.md` §1.2)
2. **레버리지 한계**: 2x 절대 초과 금지. (§1.1)
3. **paper 게이트**: backtest → walkforward → paper 7일 → 시드 10%. (§1.3, `skills/progressive-gate.md`)
4. **시크릿 평문 금지**: API key·token·password 채팅·로그·커밋 평문 노출 금지. (`doc/domains/security/rules.md` §1.1)
5. **봇 발언 신뢰성 0**: "완료했다" 발언은 즉시 신뢰 금지. CLI/`get_file_contents`로 직접 검증. SSOT는 `doc/skills/bot-ops.md` §2.2

## 문서 변경 시

새 파일 추가·기존 변경 전 `doc/skill-define.md` 우선 흡수.

- **경량 변경** (오타·예외 누적 추가·인용 보강) → §7 자기검증만
- **중량 변경** (섹션 신설·도메인 신설·파일 병합·이름변경) → 5점 체크 → Spec 7항목 사용자 승인 → 본문 → 자기검증

## 전체 본문 (자동 흡수)

@doc/README.md
@doc/skill-define.md
@doc/domains/security/rules.md
@doc/domains/trading/rules.md
@doc/domains/automation/rules.md
@doc/skills/bot-ops.md
@doc/skills/system-health.md
@doc/skills/infra-debug.md
@doc/skills/progressive-gate.md
@doc/skills/audit-log.md

## 검증 원칙 (모든 작업 공통)

- 봇·디스패치·자기 자신의 "완료했다" 발언은 신뢰 금지
- 직접 검증 = `ls -la` / `cat` / `grep` / `ps` / `curl` / `get_file_contents`
- 자기검증 4항목은 `doc/skill-define.md` §7

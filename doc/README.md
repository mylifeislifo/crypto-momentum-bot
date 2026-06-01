# Trading Harness Skills & Domain Rules

이 디렉토리는 하네스(Harness) 아키텍처의 **[2단계] 스킬** + **[3단계] 도메인 규칙** + **[메타] 스킬 정의 표준**을 모은 운영 매뉴얼이다. 클로드가 작업 전 컨텍스트로 우선 흡수하도록 설계됨.

## 구조

```
doc/
├── INDEX.md                  # [Tier 0] 카탈로그 — CLAUDE.md가 자동 흡수, 트리거 → 본문 lazy Read
├── skill-define.md           # [메타] 스킬·룰 작성·수정 표준 (Tier 1 — doc/ 변경 시)
│
├── skills/                   # 크로스커팅 스킬 (Tier 1 — 트리거 발동 시 Read)
│   ├── bot-ops.md            # 프로세스 수명주기 + 외부 발언 검증 — "신뢰성 0" SSOT
│   ├── system-health.md      # 맥미니 안정성 + 모니터링 통합
│   ├── infra-debug.md        # Tunnel/OAuth/config drift 복구 런북
│   ├── progressive-gate.md   # 비가역 행위 전 점진 진입(Sandbox→Small→Full) — 게이트 SSOT
│   ├── audit-log.md          # 구조화 로그(JSON Lines) + 직접 파싱 — 로그 인프라 SSOT
│   ├── signal-validation.md  # 정량 발견(상관·lead-lag) 자기기만 방지 검증 방법론
│   └── blog-automation.md    # WordPress 자동 포스팅(REST API + Docker + LaunchAgent) + GQ 스타일
│
└── domains/                  # 도메인별 절대 규칙 (Tier 1)
    ├── trading/rules.md      # 레버리지·수치·paper 게이트·상태 포맷·파라미터·퀀트 실행 표준
    ├── automation/rules.md   # 브라우저 기반 웹 자동화·로그인·다이얼로그
    └── security/rules.md     # 시크릿·자격증명·권한 최소화 (최상위 우선)
```

## 사용 원칙

0. **새 파일 추가·기존 변경 시 `skill-define.md` 우선** (중량 변경은 5점 체크 → Spec 7항목 승인 → 본문 → 자기검증 / 경량 변경은 자기검증만 — `skill-define.md §1.1` 참조)
1. **모든 세션이 흡수하는 것은 `INDEX.md` 단 1개** (Tier 0). 본문 룰은 INDEX 트리거 표에서 매칭되는 항목만 즉시 Read (Tier 1 lazy load)
2. **도메인 작업 진입 시** 해당 `domains/<name>/rules.md`를 즉시 Read
3. **크로스커팅 동작(배포·상태·감사·게이트·로그 등) 발생 시** `skills/<name>.md` Read
4. 각 파일의 "예외 케이스 누적" 섹션에 실전에서 학습된 룰을 지속 추가
5. 변경 시 날짜 + 변경 내용 + 사유를 해당 섹션에 기록

## 확장 시나리오

- **새 봇 추가** (bot3, bot4): `bot-ops.md` 그대로 재사용
- **새 거래소 추가** (OKX, 빗썸): `trading/rules.md`의 API 표준만 확장
- **새 자동화 사이트 추가**: `automation/rules.md`에 사이트별 섹션 추가
- **새 인프라 컴포넌트**: `infra-debug.md`에 진단 순서 추가
- **새 스킬·도메인 추가**: `skill-define.md`의 분류 룰(동사형→`skills/`, 명사형→`domains/`) 적용

## 우선순위

충돌 시 다음 순으로 적용 (`skill-define.md §6` 정의):

```
domains/security/rules.md   ← 최상위 (보안은 어떤 룰도 우회 불가)
domains/<other>/rules.md    ← 도메인 룰 (trading, automation)
skills/*.md                 ← 크로스커팅 스킬
skill-define.md             ← 메타 (작성 방법론)
INDEX.md                    ← 카탈로그 (포인터, 본 체인 외)
```

예: `bot-ops.md`의 일반 lifecycle과 `trading/rules.md`의 paper-first가 충돌하면 trading 룰을 따른다.

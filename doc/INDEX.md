# doc/INDEX.md — 카탈로그 (Tier 0 자동 흡수)

> 본 파일은 **모든 세션이 자동 흡수**하는 유일한 doc/ 본문이다. 룰을 **복제하지 않고 가리킨다.**
> 작업 영역이 잡히면 해당 본문 파일을 즉시 Read로 흡수한다.

---

## 1. 사용법 (모든 세션 공통)

1. 사용자 요청 수신
2. 아래 §3·§4·§5 트리거 표에서 작업과 매칭되는 본문 식별
3. 매칭된 본문을 **즉시 Read** (lazy load)
4. 매칭 없으면 새 영역 — `skill-define.md`도 함께 Read하여 추가 여부 판단

---

## 2. 우선순위 (충돌 시)

```
domains/security/rules.md       ← 최상위 (보안은 어떤 룰도 우회 불가)
domains/<other>/rules.md        ← trading, automation
skills/*.md                     ← 크로스커팅 스킬
skill-define.md                 ← 메타 (작성 방법론)
INDEX.md  (본 파일)             ← 카탈로그 (본문이 아닌 포인터, 우선순위 체인 외)
```

본문 룰 간 충돌은 위 체인을 따른다. INDEX 자체는 본문이 아니므로 충돌 발생 시 본문이 우선한다.

---

## 3. 도메인 인덱스 (3)

### `doc/domains/security/rules.md`
- **트리거**: 시크릿/자격증명/권한 작업, API key·token 입력, 회전·폐기, 시크릿 노출 의심
- **핵심**: §1.1 평문 노출 금지(Hard Rule) · §1.2 보관 표준(.env/Keychain/600) · §3 권한 최소화
- **우선순위**: 최상위 — 다른 룰과 충돌 시 보안 우선

### `doc/domains/trading/rules.md`
- **트리거**: 가격·주문·전략·게이트, 봇 상태 리포트, 파라미터 변경, 거래소 API, 퀀트 백테스트
- **핵심**: §1.1 레버리지 2x · §1.2 Decimal 강제 · §1.3 paper 게이트 · §5 거래 결과 검증 · §7 퀀트 실행 표준(ARM64·매크로·미국 세션)
- **인용 SSOT**: `bot-ops §2.2` (신뢰성 0) · `audit-log §2.1` (JSON Lines) · `progressive-gate §2.1`

### `doc/domains/automation/rules.md`
- **트리거**: 브라우저 기반 웹 자동화(강의 진도율·시험 응시), 다이얼로그 처리, 로그인 세션 검증
- **핵심**: §1 세션 검증 · §3 진도율 · §4 HITL(시험·결제·약관)
- **인용 SSOT**: `progressive-gate §2.4` · `security §1.1`
- **참고**: 블로그 자동 포스팅은 매커니즘이 달라 별도 — `skills/blog-automation.md`

---

## 4. 스킬 인덱스 (7)

### `doc/skills/bot-ops.md`
- **트리거**: 프로세스 시작/재시작/교체, 외부 발언(봇·디스패치·자기 자신) 검증, "X 실행/재시작/상태"
- **핵심**: §2.2 봇 발언 신뢰성 0 (**SSOT**) · §2.4 변경 전 백업 (**SSOT**) · §2.1 lifecycle
- **피인용**: trading §5 / signal-validation §2.6 / audit-log §2.3 / progressive-gate §2.5 / skill-define §7 / blog-automation §2.1

### `doc/skills/system-health.md`
- **트리거**: 맥미니 동결, 메모리 누수, cron 침묵, 워치독, 신규 장기 가동 서비스
- **핵심**: §2.1 maxvnodes · §2.2 워치독 · §2.3 모니터링 게이트웨이(Telegram/Cron)
- **인용 SSOT**: `audit-log §2.1·2.2·2.5` · `progressive-gate §2.5`

### `doc/skills/infra-debug.md`
- **트리거**: Cloudflare Tunnel 404/502/Invalid Host, OAuth state mismatch, "Incorrect API key", config drift
- **핵심**: §2.1 Tunnel 진단 순서 · §2.2 OAuth 디버깅 · §2.3 config drift 방지
- **인용 SSOT**: `bot-ops §2.4` (백업) · `security §1.2`

### `doc/skills/progressive-gate.md`
- **트리거**: 비가역·고위험 행위 직전 (실거래 전환, 시험 시작, 결제, 약관 동의, config 푸시, 데이터 삭제, 자동 publish)
- **핵심**: §2.1 Sandbox→Small→Full · §2.4 사용자 명시 승인 지점 · §2.5 자체 보고 검증 결합
- **피인용**: trading §1.3 / automation §4 / bot-ops §2.1 / system-health §2.2 / blog-automation §2.3

### `doc/skills/audit-log.md`
- **트리거**: 봇·자동화 결과 보고 시점, 봇 보고 의심·직접 파싱 필요, 새 장기 가동 서비스, 사고 사후 분석
- **핵심**: §2.1 JSON Lines 양식 (**SSOT**) · §2.3 직접 파싱 절차 · §2.4 마스킹 · §2.5 보관 주기
- **피인용**: trading §5 / system-health §2.2·2.3 / blog-automation §2.1

### `doc/skills/signal-validation.md`
- **트리거**: 정량 발견(상관·lead-lag) 신뢰·실거래 반영 직전, OOS 상관 비정상 고점, 여러 기간 창 robustness 주장
- **핵심**: §2.1 IC sanity · §2.2 겹침 표본 교정 · §2.3 중첩창 ≠ 독립 · §2.5 인과 vs 상관
- **인용 SSOT**: `bot-ops §2.2` · `audit-log §2.3` · `progressive-gate §2.5`

### `doc/skills/blog-automation.md`
- **트리거**: WordPress 블로그 자동 포스팅, 종목 분석 글, 투자일기, 카테고리 관리, LaunchAgent 등록·점검
- **핵심**: §2.1 시스템 인프라(macmini Docker) · §2.2 env var 시크릿 · §2.3 자동 publish 게이트 · §2.7 GQ 스타일
- **인용 SSOT**: `security §1.2` · `progressive-gate §2.4` · `bot-ops §2.1·2.4` · `audit-log §2.1`

---

## 5. 메타 (1)

### `doc/skill-define.md`
- **트리거**: doc/ 하위 파일 신설·삭제·이름변경·병합, 핵심 섹션 재배치, Tier 0/1 자동 흡수 구조 변경
- **핵심**: §1.1 경량/중량 구분 · §2 5점 체크 · §3 Spec 7항목 · §7 자기검증 4항목
- **우선순위**: 본문 아래 (메타)

---

## 6. surface별 적용

- **Claude Code (웹/CLI/IDE)**: `CLAUDE.md`가 본 INDEX를 `@import`로 자동 흡수 — 별도 조치 불필요
- **Claude.ai 프로젝트** 등 `CLAUDE.md` 미흡수 surface: `CLAUDE_BOOTSTRAP.md §0`이 "본 INDEX 먼저 Read → 트리거별 본문 Read" 절차를 강제

---

## 7. 예외 케이스 누적

- **[2026.06.01]** 초안 도입. `CLAUDE.md @import` 11개 → INDEX 1개로 축소(Tier 0/1 구조). 동시에 `skills/blog-automation.md` 신설 반영. drift 감시: INDEX 트리거 변경 시 본문 §1 발동 조건과 일관성 grep, 본문 신설·삭제 시 본 INDEX·CLAUDE.md 우선순위 항목·BOOTSTRAP §2 동시 갱신.

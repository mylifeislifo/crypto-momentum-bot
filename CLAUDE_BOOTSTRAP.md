# CLAUDE_BOOTSTRAP — 새 세션 진입 큐레이션

> 이 파일의 목적: **새 채팅/세션을 시작할 때 이 레포(`mylifeislifo/for_claude`)의 최신 상태를 빠르게 흡수**하기 위한 단일 진입점.
>
> - **Claude Code (웹/CLI/IDE)** 는 `CLAUDE.md`를 자동으로 읽고 `@doc/...`를 import하므로 규칙 컨텍스트가 이미 자동 흡수된다. 이 파일은 그 위에 "코드 지도 + 실행법 + 최신 상태 스냅샷"을 더한다.
> - **Claude.ai 프로젝트** 등 `CLAUDE.md`를 자동으로 읽지 않는 surface에서는 **이 파일을 가장 먼저 읽는다.** (프로젝트 지시사항에서 강제)
>
> 규칙(룰)의 단일 진리원은 항상 `CLAUDE.md` + `doc/`다. 이 파일은 규칙을 **복제하지 않고 가리킨다.** 충돌 시 `doc/` 우선.

---

## 0. 가장 먼저 할 일 — `doc/` 전체 무조건 흡수

새 세션은 **어떤 작업·답변에도 착수하기 전에 `doc/` 하위 가이드 전부를 무조건 읽는다.**
일부만 읽거나 "도메인에 맞는 것만" 고르는 것을 금지한다. 아래 11개 파일을 **전부** 흡수한다:

1. `CLAUDE.md` (루트) — 진입점, 핵심 룰 5종, 우선순위
2. `doc/README.md` — 운영 매뉴얼 구조
3. `doc/skill-define.md` — doc 변경 메타 표준
4. `doc/domains/security/rules.md` — **최상위 우선** (시크릿)
5. `doc/domains/trading/rules.md` — 레버리지·Decimal·paper 게이트·퀀트 표준
6. `doc/domains/automation/rules.md` — 웹 자동화·HITL
7. `doc/skills/bot-ops.md` — 봇 운영 + "발언 신뢰성 0" SSOT
8. `doc/skills/system-health.md` — 시스템 안정성·모니터링
9. `doc/skills/infra-debug.md` — 인프라 디버깅 런북
10. `doc/skills/progressive-gate.md` — 점진 진입 게이트
11. `doc/skills/audit-log.md` — 감사 로그 표준
12. `doc/skills/signal-validation.md` — 신호 검증 방법론 (정량 발견 자기기만 방지)

> **건너뛰지 말 것.** 위 전부를 흡수하기 전에는 트레이딩·시크릿·자동화·인프라 어떤 작업도 손대지 않는다.
>
> **surface별 적용:**
> - **Claude Code (웹/CLI/IDE)** — `CLAUDE.md`가 위 doc 파일 전부를 `@import`로 자동·무조건 흡수한다. 별도 조치 불필요. (단, doc/에 **새 파일 추가 시 `CLAUDE.md`의 `@import` 목록과 위 §0 목록을 함께 갱신**해야 자동 흡수가 유지된다.)
> - **Claude.ai 프로젝트 등 `CLAUDE.md`를 자동으로 읽지 않는 surface** — 프로젝트 지시사항에서 "이 파일을 먼저 읽고 위 10개를 전부 읽으라"고 강제한다.

그 다음: **최신 상태 직접 검증** (§3) — "봇·자기 발언 신뢰성 0" 원칙(`doc/skills/bot-ops.md §2.2`).

---

## 1. 이 레포는 무엇인가

| 항목 | 값 |
|------|-----|
| 패키지 | `btc-futures-bot` (`pyproject.toml`, v0.2.0) |
| 정체 | BTC 선물 데이트레이딩 봇 **+** 트레이딩 하네스 운영 매뉴얼(`doc/`) 통합 레포 |
| 전략 | **Confluence 3-게이트** — MACRO(센티먼트+펀딩) · MICRO(OI델타+호가 임밸런스) · CVD(누적 거래량 델타). 세 게이트가 **동시 통과**해야 시그널. LONG/SHORT 비대칭 임계값, SHORT가 의도적으로 더 엄격. (`src/bot/strategy/confluence.py`) |
| 안전 기본값 | `config/default.yaml`: `mode: paper`, `dry_run: true`, `max_leverage: 2`, `margin_mode: ISOLATED` |
| 런타임 | Python ≥3.11, Polars/Numpy 핫패스, pydantic 설정, structlog, typer CLI |

---

## 2. 코드·문서 지도 (Where things live)

### 운영 룰 (반드시 우선 흡수)
- `CLAUDE.md` — 진입점, 핵심 룰 5종
- `doc/skill-define.md` — doc 변경 시 메타 표준
- `doc/domains/security/rules.md` — **최상위**, 시크릿 평문 금지
- `doc/domains/trading/rules.md` — 레버리지 2x·Decimal 강제·paper 게이트·퀀트 실행 표준
- `doc/domains/automation/rules.md` — 웹 자동화·HITL
- `doc/skills/` — `bot-ops` / `system-health` / `infra-debug` / `progressive-gate` / `audit-log` / `signal-validation`

### 봇 코드 (`src/bot/`)
| 영역 | 경로 | 역할 |
|------|------|------|
| 진입점 | `main.py`, `cli.py` | `btcbot paper` / `btcbot live` |
| 설정 | `config/loader.py`, `config/schema.py` | YAML→pydantic |
| 코어 | `core/` | `clock`(UTC), `enums`, `logging`, `types` |
| 데이터 | `data/` | fetcher·cache·bar_builder·indicators·orderbook/oi_funding/sentiment 파이프라인·`spoof_filter` |
| 전략 | `strategy/` | `confluence`(시그널)·`aggregator`·`base` |
| 리스크 | `risk/` | `guard`(80/20 롱바이어스·일일 숏 1회·서킷)·`sizer`·`trail` |
| 실행 | `execution/` | `binance_futures`·`paper_futures`·`order_manager`·`gateway_base` |
| 알림 | `notifications/telegram.py` | |

### 설정·배포·스크립트
- `config/default.yaml`(base) → `config/paper.yaml` / `config/live.yaml`(override)
- `deploy/` — launchd plist(paper/live)·`install.sh`·`logrotate.conf`
- `scripts/` — `check_connectivity.py`·`pnl.py`·`replay.py`
- `tests/unit/` + `tests/integration/test_paper_trade_e2e.py`

---

## 3. 최신 상태 직접 검증 (스냅샷은 썩는다 — 항상 재확인)

이 섹션 표는 **검증 시점 스냅샷**이다. 새 세션마다 아래 명령으로 **직접 갱신**한다 (봇·문서·자기 발언 신뢰성 0):

```bash
git log --oneline -5            # 최신 커밋
git status                      # 작업 트리 상태
git branch -a                   # 브랜치
grep -E "^mode:|dry_run:|max_leverage:" config/default.yaml   # 안전 기본값
```

Claude.ai 프로젝트(로컬 git 없음)에서는 GitHub MCP `get_file_contents` / `list_commits`로 동일 확인.

| 항목 | 마지막 검증값 | 검증일 |
|------|---------------|--------|
| 기준 커밋(main 머지) | `6601764` (Merge PR #2) | 2026-05-24 |
| 운영 모드 기본값 | `paper` / `dry_run: true` | 2026-05-24 |
| 레버리지 상한 | `2x` | 2026-05-24 |

> 위 값과 실제 `git log`/`config`가 다르면 **실제값을 신뢰**하고 이 표를 갱신·커밋한다.

---

## 4. 실행 / 검증 명령

```bash
# 의존성 (dev 포함)
pip install -e ".[dev]"

# 페이퍼 트레이딩 (기본·안전)
btcbot paper                    # = config/default.yaml + config/paper.yaml

# 라이브 (실거래 — .env에 BINANCE_API_KEY 필요, 게이트 통과 후에만)
btcbot live

# 테스트 / 린트 / 타입
pytest
ruff check .
mypy src

# 연결성·손익·리플레이
python scripts/check_connectivity.py
python scripts/pnl.py
python scripts/replay.py
```

> **실거래(`btcbot live`) 진입은 `doc/domains/trading/rules.md §1.3` 게이트(백테스트→walkforward→paper 7일→시드 10%) + 사용자 명시 승인 없이는 금지.** (`doc/skills/progressive-gate.md`)

---

## 5. 절대 잊지 말 것 (핵심 룰 5종 요약 — 본문은 `CLAUDE.md`)

1. 가격·수량·잔고는 `decimal.Decimal`만. `float` 금지.
2. 레버리지 2x 절대 초과 금지.
3. paper 게이트: backtest → walkforward → paper 7일 → 시드 10%.
4. 시크릿(API key·token·password) 채팅·로그·커밋 평문 노출 금지.
5. 봇·자기 "완료했다" 발언 신뢰 0 — `ls`/`cat`/`grep`/`git`/`get_file_contents`로 직접 검증.

---

## 6. 유지보수

- 코드 구조·실행법이 바뀌면 §2·§4 갱신. 룰이 바뀌면 **이 파일이 아니라 `doc/`를 고치고** 여기서는 링크만 유지(드리프트 방지).
- §3 스냅샷 표는 주요 머지·설정 변경 시 갱신 후 커밋.

# [스킬] 감사 로그 표준 (Audit Log)

## 1. 발동 조건

다음 중 하나에 해당하면 이 스킬을 우선 적용한다:

- 봇·자동화·시스템 프로세스가 **외부에 보고**를 생성하는 시점 ("성공했어요", "처리 완료", "잔고 X" 등)
- **cron job·워치독·트레이딩 봇·웹 자동화** 가동 직전 (로그 인프라 사전 점검)
- 봇·에이전트 자체 보고가 의심스러워 **직접 파싱이 필요**할 때
- 새 장기 가동 서비스 추가 시 (로그 표준 적용 필수)
- 사고·이상 거래·동결 사후 분석 시 (보관된 로그가 단일 근거)

---

## 2. 준수 설계 규칙 (레시피)

### 2.1 구조화 로그 표준 (JSON Lines)

모든 보고·이벤트 로그는 **JSON Lines** 포맷으로 기록한다. 1줄 = 1 이벤트.

필수 필드:

| 필드 | 형식 | 비고 |
|------|------|------|
| `ts` | ISO 8601 UTC | 타임존 통일 (`trading/rules.md §7.3` 저장 UTC 원칙 인용) |
| `source` | 문자열 | 봇·서비스 식별자 (예: `bot_v2`, `watchdog`, `cron:hermes_prompt`) |
| `event` | 문자열 | 이벤트 종류 (예: `order_filled`, `proc_killed`, `prompt_updated`) |
| `level` | `INFO`/`WARN`/`ERROR` | 필터링용 |
| `payload` | object | 이벤트 고유 데이터 (도메인별 스키마 — 아래) |

도메인 특화 `payload` 스키마 (도메인 룰이 상세 정의, 본 스킬은 양식만):

- 거래 로그: `{symbol, side, qty, price, pnl}` — `trading/rules.md §5`
- 워치독 로그: `{pid, proc, mem_mb, action}` — `system-health.md §2.2`
- cron 로그: `{job, stdout_tail, returncode}` — `system-health.md §2.3`

### 2.2 로그 위치 표준

| 도메인 | 표준 경로 |
|--------|----------|
| 거래 로그 | `~/.local/share/<bot_name>/trades.jsonl` |
| 워치독 로그 | `~/.local/share/watchdog/*.jsonl` (`system-health §2.2`) |
| cron 출력 | `~/.hermes/cron/output/<name>.log` (`system-health §2.3`) |
| 자동화 로그 | `~/.local/share/automation/<site>/*.jsonl` |
| 외부 발언 audit | `~/.local/share/bot-ops/audit-<YYYY-MM>.jsonl` |

경로 변경 시 README 트리·인용 위치 grep 후 일괄 갱신.

### 2.3 의심 시 직접 파싱 절차

봇·에이전트가 보고한 값이 의심스러우면 **로그를 직접 파싱하여 재계산**한다:

```bash
# JSON Lines 필드별 집계 (jq)
jq -s 'map(select(.event=="order_filled")) | map(.payload.pnl|tonumber) | add' trades.jsonl

# 특정 기간 필터링
jq 'select(.ts >= "2026-05-01" and .ts < "2026-06-01")' trades.jsonl
```

```python
# Python 정밀 재계산 (Decimal 보존 — trading §1.2)
import json
from decimal import Decimal
total = sum(Decimal(str(json.loads(l)["payload"]["pnl"])) for l in open("trades.jsonl"))
print(total)
```

직접 파싱 결과와 봇 보고가 다르면 **단일 이벤트 단위로 역추적** (`trading/rules.md §5`). 본 절은 `skills/bot-ops.md §2.2` "봇 발언 신뢰성 0" SSOT의 **로그 인프라 측면** 구현이다.

### 2.4 시크릿·민감 정보 마스킹

로그 작성 직전 다음을 **자동 마스킹**한다:

- API key / secret / token: 앞 4자리 + `***` + 뒤 4자리
- 비밀번호 / OAuth refresh token: 전체 `***`
- 개인정보(주민번호·연락처·결제 정보): 전체 `***`

위반 시 `security/rules.md §1.1`(평문 노출 금지) 위반 → 로그 파일 즉시 폐기 + 자격증명 회전.

```python
def mask_secret(s: str) -> str:
    return "***" if len(s) <= 8 else s[:4] + "***" + s[-4:]

logger.info("api call", extra={"key": mask_secret(api_key)})
```

### 2.5 보관 주기

| 등급 | 보관 | 대상 |
|------|------|------|
| **장기 (90일)** | 90일 이상 | 거래 로그, 자격증명 회전 이력, 시크릿 audit |
| **중기 (30일)** | 30일 | 워치독, cron 출력, 자동화 결과 |
| **단기 (7일)** | 7일 | 일반 INFO 로그, 진단 출력 |

보관 종료 시 자동 압축 + archive 디렉토리 이동. **즉시 삭제 금지** (사고 사후 분석 가능성).

---

## 3. 예외 케이스 누적 (지속 업데이트)

- **[2026.05.23]** 초안 도입. 거래 로그(`trading §5`)·워치독 로그(`system-health §2.2`)·cron 출력(`system-health §2.3`)의 JSON Lines 표준이 분산되어 있어 SSOT로 통합. 도메인별 구체 payload 스키마는 도메인 룰에 유지하고, 본 스킬은 공통 양식·위치·파싱·마스킹·보관 표준만 명문화.

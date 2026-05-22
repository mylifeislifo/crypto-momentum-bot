# [스킬] 시스템 안정성 + 모니터링 (System Health)

## 1. 발동 조건

다음 중 하나에 해당하면 이 스킬을 우선 적용한다:

- 맥미니 **장기 가동(>7일)** 후 동결·앱 실행 불가 증상이 발생했을 때
- Claude Code, Cowork Dispatch, Hermes 등 **알려진 메모리 누수 프로세스**가 가동 중일 때
- **Telegram·Cron 기반 정기 보고**가 침묵하거나 비정상일 때
- **새로운 장기 가동 서비스**를 추가할 때
- 사용자가 "맥미니 상태 점검", "메모리 확인" 요청 시

---

## 2. 준수 설계 규칙 (레시피)

### 2.1 맥미니 안정성 기본 설정

장기 가동 시 vnode 고갈로 인한 앱 실행 불가를 방지:

```bash
# 즉시 적용
sudo sysctl -w kern.maxvnodes=600000

# 영구 적용
echo "kern.maxvnodes=600000" | sudo tee -a /etc/sysctl.conf
```

추가:
- **주 3회** 알려진 메모리 누수 앱(Claude Code, Dispatch) 자동 재시작 (LaunchAgent 또는 cron)
- 시스템 부팅 시 핵심 서비스(cloudflared, watchdog, 트레이딩 봇) 자동 시작 설정 확인

### 2.2 메모리 누수 워치독 표준

5분 주기로 알려진 누수 프로세스 메모리 사용량을 측정 → 임계치 초과 시 자동 종료:

| 항목 | 표준값 |
|------|--------|
| 체크 주기 | 5분 |
| 임계치 | 4GB (조정 가능, 시스템 동결 전 단계) |
| False positive 방지 | 종료 결정 전 **1회 재측정** |
| 로그 위치 | `~/.local/share/watchdog/` |
| 로그 형식 | JSON Lines |
| 보관 기간 | 최소 30일 |

종료 후 알림(Telegram) 발송으로 사용자가 인지하도록 함.

### 2.3 모니터링 게이트웨이 (Telegram / Cron)

#### Telegram 봇
- 본인 `user_id`만 화이트리스트로 허용. 그 외 메시지는 **전부 차단**
- 환경변수 또는 keychain에서 토큰 로드 (코드 평문 금지 — `security/rules.md` 참조)

#### Cron 작업
신규 등록 시 다음 3종을 **반드시 함께** 명시한다:

1. **실행 주기** (예: 30분)
2. **실행 결과 로그 경로** (예: `~/.hermes/cron/output/<name>.log`)
3. **실패 시 알림 채널** (Telegram 등)

#### 침묵 감지
예상 보고가 **(주기 × 2)** 이상 지연되면 자동 경보를 발송한다. 예: 30분 주기 보고가 60분 침묵 시 alert.

### 2.4 정기 헬스체크 권장 항목

| 항목 | 명령어 / 확인 방법 |
|------|---------------------|
| 디스크 여유 | `df -h /` |
| 메모리 압박 | `vm_stat \| head -10` |
| 누적 부하 | `uptime` (load average) |
| 핵심 프로세스 가동 | `ps aux \| grep -E "<bot1>\|<bot2>\|hermes\|cloudflared"` |
| Tunnel 외부 도달 | `curl -I https://<your-host>/` |
| 시계 동기화 | `sntp -sS time.apple.com` |

---

## 3. 예외 케이스 누적 (지속 업데이트)

- **[2026.05.12]** `kern.maxvnodes` 미설정 상태로 7일 가동 → 앱 실행 불가 동결. 영구 설정 후 1년 가동 안정성 확보
- **[2026.05.21]** Hermes cron이 prompt 변경 후 결과 누락 → cron 실행 이력은 `~/.hermes/cron/output/` 직접 확인. CLI 보고만 신뢰 금지(bot-ops.md 2.2 audit 원칙 적용)
- **[2026.05.21]** Claude Code / Dispatch 메모리 4GB 임계치 워치독 도입 → 시스템 동결 0건

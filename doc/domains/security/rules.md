# 보안 도메인 규칙 (rules.md)

이 도메인은 모든 다른 도메인 위에 위치한다. 트레이딩·자동화·인프라 어떤 작업도 이 규칙을 우회할 수 없다.

---

## 1. 시크릿 관리 절대 원칙

### 1.1 평문 노출 금지 (Hard Rule)

다음을 **채팅·로그·커밋·이슈·티켓**에 평문으로 입력 금지:

- 거래소 API key / Secret key (바이낸스, 업비트, 빗썸 등)
- AI provider API key (Anthropic, OpenAI, xAI 등)
- Telegram bot token
- OAuth 토큰 / refresh token
- 비밀번호
- Private key (SSH, GPG, JWT)
- 데이터베이스 접속 문자열 (자격증명 포함)

**위반 발생 시 즉시 다음을 수행한다**:

1. 해당 자격증명 즉시 **폐기 + 재발급** (1순위 — public repo에 올라가면 일부만 회수 가능성을 고려)
2. 노출된 로그·메시지·커밋 이력 삭제 (`git filter-repo` 등) — 폐기대비 효과 제한적 (이미 fork/clone되었으면 완전 회수 불가)
3. 사고 일자·노출 범위·후속 조치를 `예외 케이스 누적` 섹션에 기록

### 1.2 표준 보관 위치

| 항목 | 권장 보관 |
|------|----------|
| 로컬 개발 | `.env` 파일 (`.gitignore` 강제) |
| 로컬 장기 보관 | macOS Keychain (`security add-generic-password`) |
| 운영(클라우드) | Cloudflare / AWS / GCP Secret Manager |
| 컨테이너 | 환경변수 주입 (이미지 빌드 시 포함 금지) |
| 파일 권한 | `chmod 600` — `.env`, `~/.<service>/auth.json`, Keychain 로컬 백업, SSH private key. 그 외 사용자 이외 읽기 가능한 자격증명 파일 금지 |

### 1.3 코드 내 참조 표준

```python
import os

# OK — 환경변수 로드
api_key = os.environ["EXCHANGE_API_KEY"]

# OK — Keychain 로드 (macOS)
import subprocess
api_key = subprocess.check_output(
    ["security", "find-generic-password", "-w", "-s", "exchange-api-key"]
).decode().strip()

# 금지 — 평문 하드코딩
api_key = "sk-abc123..."

# 금지 — 주석에 키 남기기
# 테스트용: sk-test-...
```

---

## 2. 자격증명 회전 주기

| 자격증명 | 주기 | 비고 |
|----------|------|------|
| 거래소 API key | 90일 | 자동 알림 + 수동 회전 |
| 거래소 출금 권한 키 | 사용 금지 (운영) | 별도 보관, 인출 시에만 임시 발급 |
| Telegram bot token | 누출 의심 시 **즉시** | BotFather에서 `/revoke` |
| OAuth refresh token | provider 정책 준수 | 보통 자동 갱신 |
| SSH key | 1년 | passphrase 필수 |

---

## 3. 권한 최소화 (Principle of Least Privilege)

### 3.1 거래소 API
- **read-only로 가능한 작업**은 read-only key 사용
  - 잔고 조회, 호가창 조회, 백테스트용 데이터 → read-only
  - 주문·취소 → trade 권한 (별도 key)
- **출금 권한은 운영 봇에서 영구 제외**. 활성화 자체를 거래소 설정에서 차단
- **IP whitelist 적용 가능한 경우 반드시 적용**
  - 맥미니 고정 IP 또는 Tunnel 출구 IP로 제한

### 3.2 시스템
- `sudo` 사용은 명령어 단위로 명시. 무제한 sudo 셰 사용 금지
- 봇 프로세스는 일반 사용자 권한으로만 가동 (root 금지)

---

## 4. 시크릿 의심 시 대응 절차

채팅·로그·커밋에 시크릿이 노출되었거나 의심되면:

1. **즉시 폐기·재발급** (분석은 그 다음)
2. 노출 경로 파악 (어디서 어디로 흘렀나)
3. 노출된 자격증명으로 가능했던 행위 범위 점검 (이상 거래·접근 로그 확인)
4. 본 파일의 `예외 케이스 누적`에 기록
5. 동일 사고 재발 방지책 1개 이상 도입

---

## 5. 예외 케이스 누적

- **[2026.05]** 채팅 평문으로 Telegram bot token 및 chat_id 전달 사례 다수 → 향후 zsh history 마스킹, 클립보드 매니저 자동 삭제, `.env` 파일 사용 의무화
- **[2026.05]** 거래소 API key가 채팅 전달 시 메시지 즉시 삭제 + 본 도메인 규칙 도입. 향후 비밀 입력 시 채팅이 아닌 별도 채널(.env 파일 업로드 또는 Keychain 직접 등록) 활용

---

## 6. 점검 체크리스트 (월 1회 수행 권장)

- [ ] 활성 API key 목록 검토 — 미사용 키 폐기
- [ ] IP whitelist 누락 거래소 점검
- [ ] `.env` 파일이 git에 commit되지 않았는지 확인
- [ ] Telegram 봇 접근 user_id 화이트리스트 유효성 확인
- [ ] OAuth 토큰 만료일 확인
- [ ] 자격증명 파일 권한 `chmod 600` 유지 여부 점검

# [스킬] 인프라 디버깅 런북 (Infrastructure Debug)

## 1. 발동 조건

다음 중 하나에 해당하면 이 스킬을 우선 적용한다:

- Cloudflare Tunnel 경유 외부 접속이 **404 / 502 / Invalid Host** 응답을 반환할 때
- **OAuth 인증이 state mismatch / token invalid**로 실패할 때
- 데몬·서비스 재시작 후 **이전과 다른 config**로 가동되는 의심이 있을 때 (config drift)
- API key가 유효한데도 **"Incorrect API key" 에러**가 발생할 때 (provider 설정 오류 가능성)
- LaunchAgent / launchctl 서비스가 의도와 다르게 동작할 때

---

## 2. 준수 설계 규칙 (레시피)

### 2.1 Cloudflare Tunnel 진단 표준 순서

문제 발생 시 다음 순서로 진단:

1. **외부 응답 헤더 확인**
   ```bash
   curl -iL https://<host>/ 2>&1 | head -40
   curl -v https://<host>/ 2>&1 | grep -E "^[<>] " | head -30
   ```
2. **로컬 바인딩 확인** (서비스가 실제 listening 중인가)
   ```bash
   lsof -iTCP -sTCP:LISTEN | grep <port>
   ```
3. **Tunnel config 확인** (system 영역)
   ```bash
   sudo cat /etc/cloudflared/config.yml
   ```
4. **ingress 매칭 확인**: host header가 dashboard 기대값과 일치하는지
5. **httpHostHeader 설정**: dashboard가 `127.0.0.1`을 기대하면 ingress에 추가
   ```yaml
   - hostname: <your-host>
     service: http://127.0.0.1:<port>
     originRequest:
       httpHostHeader: 127.0.0.1
   ```
6. **데몬 재시작 후 적용 검증**: 재시작 자체가 config를 갈리게 할 수 있으므로(예외 케이스 참조) 반드시 직접 확인

### 2.2 OAuth 디버깅 표준

- **기존 인증 잔재 삭제**: `rm -f ~/.<service>/auth.json`
- **Provider 종류 명시적 확인**: `oauth` vs `api-key` 구분 (config에서 직접 확인)
- **state mismatch**는 보통 SSH 환경 + 로컬 브라우저 콜백 불일치가 원인
  - 해결: 인증은 **동일 머신**(로컬 GUI)에서 수행
  - 또는 device-flow 옵션 사용 (provider 지원 시)
- 인증 성공 후 토큰 파일 권한 확인: `chmod 600 ~/.<service>/auth.json`

### 2.3 Config Drift 방지

데몬 재시작 시 다음 3종을 **반드시 직접 확인**:

1. **config 파일 경로** — 시스템 영역(`/etc/`) vs 사용자 영역(`~/.config/`) 구분
2. **실제 로드된 config** — CLI `<service> config show` (메모리 상 값)
3. **환경변수 override 여부** — `env | grep <SERVICE>_`

변경 전 백업은 **필수**:
```bash
cp <config> <config>.before_<change>_$(date +%Y%m%d_%H%M%S)
```

### 2.4 진단 결과 보고 표준

사용자에게 보고 시 다음 4종을 한 번에 제공:

1. **증상** (외부에서 보이는 현상)
2. **원인** (확인된 사실 + 추정)
3. **수행한 변경** (백업 경로 포함)
4. **검증 명령** (사용자가 직접 확인할 수 있는 1줄 명령)

---

## 3. 예외 케이스 누적 (지속 업데이트)

- **[2026.05]** cloudflared 데몬 재시작 후 hermes ingress의 `httpHostHeader` 설정이 무시되는 사례 → 데몬을 사용자 영역과 시스템 영역에서 중복 가동한 것이 원인. 시스템 영역 cloudflared로 통일 후 해결
- **[2026.05]** OAuth 토큰이 살아있는데 "Incorrect API key" 에러 발생 → config의 `provider` 필드가 `oauth`에서 `api-key`로 갈린 사례. dashboard 재시작 중 config 갈림으로 추정. provider 필드 직접 확인 + 재설정
- **[2026.05]** 외부에서 "Invalid Host header" 응답 → dashboard가 `127.0.0.1`을 기대하는데 Cloudflare가 외부 hostname을 그대로 전달. ingress `httpHostHeader: 127.0.0.1` 추가로 해결

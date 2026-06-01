# [스킬] 블로그 자동화 (Blog Automation)

## 1. 발동 조건

다음 중 하나에 해당하면 이 스킬을 우선 적용한다:

- WordPress 블로그에 **자동 포스팅** 요청 (종목 분석, 투자일기, 일반 글)
- 포트폴리오 **스크린샷 첨부 + "투자일기" 언급**
- 블로그 **카테고리 관리·정리** 요청
- **LaunchAgent로 정기 자동 글 발행** 설정·점검·재시작
- "블로그에 올려줘", "포스팅해줘", "오늘 분석 돌려줘" 등의 발화

---

## 2. 준수 설계 규칙 (레시피)

### 2.1 시스템 인프라

| 항목 | 값 / 위치 |
|------|----------|
| 호스트 | macmini 로컬 |
| WP 컨테이너 | `blog-wordpress-1` (`~/blog/docker-compose.yml`) |
| 분석 스크립트 | `~/blog-auto-post/stock_analyzer.py` |
| 자동 실행 | `com.blog.stock.analyzer` LaunchAgent (매일 08:00 KST) |
| 로그 | `~/blog-auto-post/analyzer.log` — JSON Lines (`skills/audit-log.md §2.1` SSOT) |
| LLM 프록시 | `localhost:4000` litellm (Grok) |
| Cloudflare Tunnel | 시스템 서비스 `/Library/LaunchDaemons/com.cloudflare.cloudflared.plist` (`infra-debug §2.1`) |

LaunchAgent 시작·재시작 후 **5초 내 로그 확인**으로 정상 가동 검증 (`skills/bot-ops.md §2.1 #6`). 변경 전 백업은 `skills/bot-ops.md §2.4`.

### 2.2 자격증명 (env var 표준)

모든 시크릿은 `~/.zshenv`의 환경변수로만 보관. 코드·본 문서·로그 어디에도 평문 노출 금지 (`security/rules.md §1.1·§1.2`):

| env var | 용도 |
|---------|------|
| `WP_BLOG_URL` | WordPress 블로그 주소 |
| `WP_USER` | WordPress 관리자 ID |
| `WP_APP_PASS` | WordPress Application Password (REST API 인증) |
| `CF_API_TOKEN` | Cloudflare DNS-only API 토큰 |
| `TELEGRAM_BOT_TOKEN` | 알림 봇 토큰 |
| `TELEGRAM_CHAT_ID` | 수신 chat ID — **env var 참조만**, 본 문서·로그·이슈에 평문 금지 |
| `XAI_API_KEY` | xAI API 키 |

회전·폐기 절차는 `security/rules.md §2·§4` 표준 따름. 로그 작성 시 마스킹은 `skills/audit-log.md §2.4`.

### 2.3 자동 publish는 비가역 — progressive-gate 적용

새 자동 글 발행 로직·신규 카테고리·신규 사이트 진입은 다음 게이트를 순차 통과 (`skills/progressive-gate.md §2.1·§2.4`):

| 단계 | 조건 |
|------|------|
| **Sandbox** | `--dry-run` 또는 `status="draft"`로 글 작성, REST API 응답·payload만 확인 |
| **Small** | `status="publish"`로 **수동 1건**만 발행. 글 URL을 사용자 채팅에 보고하고 승인받음 |
| **Full** | LaunchAgent 자동 실행 등록 — **사용자 명시 승인 필수** |

이미 발행된 글의 **자동 수정·삭제도 비가역**으로 간주. "글 지워줘"·"수정해줘" 발화를 받아도 **URL·제목·시점을 사용자에게 확인한 뒤** 실행.

### 2.4 종목 분석 자동 포스팅 (스크립트 흐름)

`~/blog-auto-post/stock_analyzer.py`가 수행:

1. yfinance로 3개월 OHLCV 수집
2. 차트 3개 PNG 생성 (`/tmp/stock_charts_v2/`)
   - `_1_price.png` — 캔들스틱 + MA20/MA60
   - `_2_rsi.png` — 볼린저밴드 + RSI(14)
   - `_3_levels.png` — 지지·저항선
3. Grok(`localhost:4000`)으로 §2.7 GQ 스타일 분석글 생성
4. WP REST API로 이미지 3개 업로드 (`/wp-json/wp/v2/media`)
5. 포스트 생성 (`/wp-json/wp/v2/posts`, 카테고리 ID=`종목분석`)

종목 추가·변경은 스크립트 상단 `TICKERS` 딕셔너리 편집. 풀 변경은 §2.3 게이트 따름.

수동 실행:
```bash
cd ~/blog-auto-post && source ~/.zshenv && python3 stock_analyzer.py
```

자동 실행 확인:
```bash
launchctl list | grep com.blog.stock
cat ~/Library/LaunchAgents/com.blog.stock.analyzer.plist
```

### 2.5 투자일기 포스팅

포트폴리오 스크린샷에서 추출:
- 총 자산 / 원금 / 총 수익(%) / 일간 수익(%)
- 종목별: 티커·수량·평단가·현재가·평가금액·수익률
- 숨긴 종목(워런트 등)

REST API 패턴 — **env var 사용 강제, 평문 키 절대 금지**:

```python
import os, base64, requests
from datetime import datetime

auth = base64.b64encode(
    f"{os.environ['WP_USER']}:{os.environ['WP_APP_PASS']}".encode()
).decode()
headers = {"Authorization": f"Basic {auth}", "Content-Type": "application/json"}

today = datetime.now().strftime("%Y년 %m월 %d일")
payload = {
    "title": f"[투자일기] {today} — 제목",
    "content": "<html 본문>",
    "status": "publish",
    "author": 1,
    "categories": [4],  # 투자일기
}
r = requests.post(
    f"{os.environ['WP_BLOG_URL'].rstrip('/')}/wp-json/wp/v2/posts",
    headers=headers, json=payload,
)
```

### 2.6 카테고리 ID 참조

| 카테고리 | ID | 용도 |
|---------|----|----|
| 미분류 | 1 | 기본 |
| 종목분석 | 3 | 주가 전망·투자 포인트 분석글 |
| 투자일기 | 4 | 포트폴리오 현황·감정 일기 |
| 일상 | 5 | 개인 에세이·생각 |

신규 카테고리·신규 사이트 추가는 §2.3 게이트.

WP-CLI 직접 포스팅 대안:
```bash
docker exec blog-wordpress-1 wp post create \
  --post_title="제목" --post_content="내용" \
  --post_status=publish --post_author=1 --allow-root
docker exec blog-wordpress-1 wp media import /tmp/image.png --allow-root --porcelain
```

### 2.7 GQ Korea 글쓰기 스타일

투자 블로그는 다음 패턴을 따른다:

**헤드라인**
- 숫자 리스트형: `~하는 이유 5`, `~하는 순간 7`, `~하는 습관 8`
- 반전/의외성: `의외의`, `따로 있다`, `의외로 도움이 된다`
- 구어체 선언형: `분명 ~했는데, 결국 ~하게 된다`
- 실용 팁형: `~하는 법`, `~하면 생기는 일`

**문장**
- **첫 문장**: 독자가 오늘 이미 겪은 장면/감정을 한 줄로 찍기
- **길이**: 한 줄이면 충분할 때 두 줄 쓰지 않기. 끊어서 리듬
- **소제목 공식**: 현상 묘사 → 이유/근거 → 짧은 반전/결론

**어휘**
- 연결어: `사실상`·`은근히`·`이상하게`·`근데 생각해보면`·`그러니까`·`결국`·`딱 그 순간`·`바로 그게`
- 영어 자연 혼용: 텐션·타이밍·모멘텀·밸류에이션·포지션·라운드
- 전문 용어는 등장 시 괄호 해설 — 예: `지지선(주가가 더 내려가지 않는 가격대)`, `RSI(주가 과열 여부를 0~100으로 나타내는 지표)`

**마무리**
- 단정 짓지 않기 — 결론보다 질문이나 여운으로
- ❌ "따라서 OPEN은 매수 적기입니다"
- ✅ "지지선이 버텨준다면, 이 가격이 나중에 '그때 샀어야 했는데'가 될 수도 있다"

**글 구조 (종목 분석)**
```
[오늘 장의 한 줄 감정] → 종목 소개 → 차트 1 →
기술적 분석(짧게) → 재무/이슈 → 차트 2 →
투자 포인트 → 리스크 → 차트 3 → 전망 + 면책조항
```

**글 구조 (투자일기)**
```
[오늘 장 끝나고 느낀 감정 한 줄] → 포트폴리오 표 →
종목별 솔직한 생각 → 앞으로의 전략 → 여운 있는 마무리
```

시스템 프롬프트는 `~/blog-auto-post/stock_analyzer.py` 내 `SYSTEM_PROMPT` 변수.

### 2.8 점검 명령어

```bash
# 컨테이너 상태
docker compose -f ~/blog/docker-compose.yml ps

# 최근 로그
tail -f ~/blog-auto-post/analyzer.log

# REST API 헬스체크 (env var 사용)
source ~/.zshenv
curl -s -H "Authorization: Basic $(echo -n "$WP_USER:$WP_APP_PASS" | base64)" \
  "$WP_BLOG_URL/wp-json/wp/v2/posts?per_page=5&_fields=id,title" | python3 -m json.tool
```

LaunchAgent·봇의 "성공" 자체 보고는 신뢰 금지(`skills/bot-ops.md §2.2` SSOT). 위 명령으로 직접 확인한다.

---

## 3. 예외 케이스 누적

### 3.1 사이트별 패턴 (singingsand.space)

- **블로그 URL**: `https://blog.singingsand.space`
- **WP 관리자**: `singingsand`
- **기본 종목 풀**: OPEN, IREN, PLTR, TSLA, TSSI (변경 시 §2.3 게이트)
- **REST endpoint**: `/wp-json/wp/v2/{posts,media,categories,...}`
- **시스템 프롬프트 위치**: `~/blog-auto-post/stock_analyzer.py` 내 `SYSTEM_PROMPT`

### 3.2 신규 사이트 추가 시 절차

`automation/rules.md §3.1`의 "1개씩 순차, 동시 다발 금지" 원칙을 따른다. §2.3 progressive-gate(Sandbox→Small→Full) 통과 후 본 §3에 사이트별 패턴 절(§3.x)을 신설하고 본문 §2는 일반 표준만 유지한다.

### 3.3 변경 누적

- **[2026.06.01]** 초안 도입. 외부 첨부(`blog-automation.skill` ZIP — `SKILL.md` + `references/gq_style.md`)를 `skills/<name>.md` 양식으로 흡수. 5점 체크 통과(단일 사이트는 §3.1로 격리, env var 외부화 유지, **원본 `TELEGRAM_CHAT_ID` 평문 노출 제거 — env var 이름만 보존**). Spec 7 승인 완료. CLAUDE.md `@import` 폐기 + INDEX 카탈로그 추가 동시 반영.

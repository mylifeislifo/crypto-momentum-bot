# Trading Harness Skills & Domain Rules

이 디렉토리는 하네스(Harness) 아키텍처의 **[2단계] 스킬** + **[3단계] 도메인 규칙**을 모은 운영 매뉴얼이다. 클로드가 작업 전 컨텍스트로 우선 흡수하도록 설계됨.

## 구조

```
docs/
├── skills/                   # 크로스커팅 스킬 (도메인 무관, 동사 단위)
│   ├── bot-ops.md            # 프로세스 수명주기 + 외부 발언 검증(audit)
│   ├── system-health.md      # 맥미니 안정성 + 모니터링 통합
│   └── infra-debug.md        # Tunnel/OAuth/config drift 복구 런북
│
└── domains/                  # 도메인별 절대 규칙 (rules.md)
    ├── trading/rules.md      # 레버리지·수치·paper 선검증·상태 포맷·파라미터
    ├── automation/rules.md   # 웹 자동화·로그인·다이얼로그
    └── security/rules.md     # 시크릿·자격증명·권한 최소화
```

## 사용 원칙

1. **도메인 작업 진입 시** 해당 `domains/<name>/rules.md`를 컨텍스트에 우선 흡수
2. **크로스커팅 동작(배포·상태·감사 등) 발생 시** `skills/` 참조
3. 각 파일의 "예외 케이스 누적" 섹션에 실전에서 학습된 룰을 지속 추가
4. 변경 시 날짜 + 변경 내용 + 사유를 changelog 섹션에 기록

## 범용성 보장

- **새 봇 추가** (bot3, bot4): `bot-ops.md` 그대로 재사용
- **새 거래소 추가** (OKX, 빗썸): `trading/rules.md`의 API 표준만 확장
- **새 자동화 사이트 추가**: `automation/rules.md`에 사이트별 섹션 추가
- **새 인프라 컴포넌트**: `infra-debug.md`에 진단 순서 추가

## 우선순위

도메인 규칙(`rules.md`)이 크로스커팅 스킬(`skills/*.md`)보다 **우선**한다.
예: `bot-ops.md`의 일반 lifecycle과 `trading/rules.md`의 paper-first가 충돌하면 trading 룰을 따른다.

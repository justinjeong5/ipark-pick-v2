# Proposal: Naver Cafe Comment Bot — Phase 1 (Comment Posting)

- **Date**: 2026-05-03
- **Status**: Completed
- **Phases**: Phase 1 + Phase 2 운영 단계 진입 (2026-05-03)
- **Author**: justin.jeong@buzzvil.com
- **Stage**: 1 of N (this proposal covers Phase 1 only)

## 1. Background / Why

i-PARK 부동산은 공실이 발생할 때마다 네이버 카페에서 추첨을 진행한다. 평일 오전 10:00:00 ~ 10:00:59 사이에 댓글(`생년월일 + 이름`)을 작성한 사용자 중 추첨이 이루어지며, 14:30에 결과가 발표된다. 1분 윈도우는 사람이 매일 정확히 맞추기 어렵고, 3개 아이디를 모두 등록하려면 사실상 자동화가 필수다.

전체 시스템의 가장 어렵고 위험한 부분은 **댓글 작성 자동화 자체**(봇 탐지 우회, 로그인, iframe 진입, 폴링 타이밍)이므로, Phase 1에서는 이것만 분리해 검증한다. 검증 통과 시 Phase 2(스케줄·다중계정·텔레그램·당첨확인)는 단순 결합 작업이 된다.

## 2. Goals (Phase 1)

- 1개 네이버 아이디로 **테스트 카페 게시글**에 댓글을 작성하는 핵심 기능을 동작시킨다.
- 네이버의 자동화 탐지를 우회한 상태로 안정적으로 댓글 등록까지 도달.
- 댓글창이 막혀 있을 때를 감지하고 정상 종료한다 ("추첨 없음" 시그널 = 댓글창 비활성).
- 모든 단계가 CLI로 수동 실행 가능 (스케줄러는 Phase 2).
- 핵심 로직(폴링/상태판정/에러분기)에 단위 테스트.

### Non-Goals (Phase 2 이후)

- 스케줄링(launchd)
- 텔레그램 알림
- 당첨 결과 확인
- 3개 아이디 동시 처리
- 게시글 ID 자동 탐색 (Phase 1은 고정 ID 사용)

## 3. Approach

### 3.1 기술 스택

- Python 3.11+
- Playwright (Chromium) + `tf-playwright-stealth`
- 표준 logging
- pytest + pytest-asyncio

### 3.2 로그인 전략 — Cookie 세션 모드

네이버는 자동 로그인 시 거의 100% 캡챠를 띄운다. 따라서:

1. **첫 실행**: `ipark-drawing login --account N` → 브라우저 창이 뜨고 사용자가 직접 로그인 → 로그인 완료 후 터미널에서 Enter → 쿠키를 `data/cookies/account_N.json`에 저장
2. **이후 실행**: 저장된 쿠키 로드 → 즉시 인증 상태로 진입
3. **만료 감지**: 페이지 진입 시 로그인 상태를 확인. 만료면 명확한 예외로 종료(Phase 2에서 텔레그램 알림 연결).

쿠키는 `data/cookies/`에 평문 저장 (gitignore). 로컬 사용 가정.

### 3.3 댓글 작성 알고리즘

```
1. 컨텍스트 생성 (cookies 로드, stealth 적용)
2. 카페 게시글 URL 진입
3. 카페 iframe (`#cafe_main`) 안으로 frame 전환
4. 폴링 루프 (100ms 간격, 최대 COMMENT_POLL_TIMEOUT 초):
   - 댓글 textarea 가 활성 상태인가?
     - 활성: 작성 단계로 진입
     - 비활성: 다음 틱
5. 작성 단계:
   - textarea click → fill(comment_text) → submit 버튼 click
   - 자연스러운 타이핑 시간 추가 (50-100ms/char)
6. 검증: 내가 방금 작성한 텍스트가 댓글 목록에 보이는가?
7. 결과 dataclass 반환:
   - status: posted | skipped_blocked | failed
   - reason, timing_ms, screenshot_path
```

### 3.4 Stealth 적용

`tf-playwright-stealth.stealth_async(page)`를 컨텍스트 생성 직후 호출. 추가로:
- `locale = ko-KR`
- `timezone = Asia/Seoul`
- 한국어 User-Agent
- viewport 1280x800 (자연스러운 노트북 사이즈)

### 3.5 모듈 분리

| 모듈 | 책임 |
|------|------|
| `config.py` | 환경변수 로드, `Account` 데이터클래스, selector 상수 |
| `browser.py` | Playwright 브라우저/컨텍스트 생성 (stealth 포함) |
| `naver_auth.py` | 쿠키 저장/로드, 로그인 상태 검증, 첫 로그인 플로우 |
| `comment_bot.py` | iframe 진입, 폴링, 작성, 검증, `CommentResult` 반환 |
| `cli.py` | `login` / `comment` 서브커맨드 |

각 모듈 100줄 미만을 목표.

## 4. Risks & Mitigations

| 리스크 | 대응 |
|------|------|
| 봇 탐지 → 캡챠 | stealth + 첫 로그인을 사용자가 수동 처리 + 자연스러운 타이밍 |
| 로그인 만료 | 페이지 진입 시 상태 검증 → 명확한 예외 |
| iframe selector 변경 | selector를 `config.py` 상수로 분리 |
| 폴링 중 페이지 멈춤 | 전체 타임아웃 + WebSocket reconnect 시도 안 함 (단순화) |
| 캡챠 발견 | 즉시 중단 + 스크린샷 저장 + 명시적 에러 |
| 댓글 중복 등록 | 작성 직전 "내 ID의 댓글이 이미 있나" 확인 |

## 5. Decision Log

- **언어/도구**: Python + Playwright (사용자 합의)
- **실행 환경**: 로컬 macOS launchd (사용자 합의, 노트북 항상 켜둠)
- **게시글 ID 추적**: 자동 탐색 — 단, Phase 1은 고정 ID(`articleId=21`)로 검증
- **구현 범위**: 댓글 작성 기능만 (사용자 합의)
- **로그인 방식**: 쿠키 세션 모드 (자동 로그인 X, 사용자 1회 수동 로그인 후 재사용)
- **Stealth**: `tf-playwright-stealth` (active fork)

## 6. Implementation Summary (filled after merge)

_pending_

# ipark-drawing

매주 목요일 10:00:00~10:00:59 네이버 카페 댓글 자동 작성 봇 (i-PARK 부동산 공실 추첨용).

## 현재 단계

운영 단계 — 게시글 ID 자동 탐색 + 3계정 병렬 댓글 + 텔레그램 알림 + 당첨자 확인 + launchd 4-잡 스케줄(notice / heartbeat / morning / winners).

## 셋업

### 1) Python 환경

```sh
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
playwright install chromium
```

### 2) 환경 변수

`env.example`을 복사해서 `.env` 만들고 값 채우기.

```sh
cp env.example .env
# 편집기로 .env 열어서 NAVER_ACCOUNT_1_* 채우기
```

> Hook으로 `.env`를 Claude가 직접 만들 수 없어 사용자가 직접 만드셔야 합니다.

`.env`에 넣을 값 (`env.example` 참고):

| 키 | 설명 |
|----|------|
| `NAVER_ACCOUNT_1_ID` ~ `..._3_ID` | 네이버 아이디 (3계정 모두 필수) |
| `NAVER_ACCOUNT_1_COMMENT` ~ `..._3_COMMENT` | 댓글 텍스트 (`이름+생년월일4자리`, 예: `정창우1125`) |
| `TARGET_CLUB_ID` | 카페 URL의 `/cafes/<숫자>` 부분 |
| `TARGET_LIST_MENU_ID` | 게시판 자동 탐색 메뉴 ID (`0` = 전체글, 권장) |
| `TELEGRAM_BOT_TOKEN` | BotFather 발급 토큰 (`12345:AAA...`) |
| `TELEGRAM_CHAT_ID` | 비공개 그룹은 `-100<id>` 형태 |
| `HEADFUL` | `true`=브라우저 표시(디버깅), `false`=백그라운드(운영, 기본) |
| `COMMENT_POLL_TIMEOUT` | 댓글창 폴링 타임아웃 초 (기본 60) |

### 3) 첫 로그인 (쿠키 저장)

네이버는 자동 로그인을 강하게 막습니다. **첫 실행 시 사용자가 직접 브라우저에서 로그인**해서 세션을 저장하는 방식을 씁니다.

```sh
ipark-drawing login --account 1
```

브라우저 창이 뜨면:
1. 네이버 로그인 페이지에서 **수동으로 로그인** (캡챠도 직접 통과)
2. 로그인 완료되면 터미널에서 Enter 키 입력
3. 쿠키가 `data/cookies/account_1.json`에 저장됨

이후 실행은 저장된 쿠키로 자동 인증됩니다. 만료되면 다시 `login` 명령으로 갱신.

### 4) 단일 계정 댓글 테스트

```sh
# 화면 보이는 모드로 테스트
HEADFUL=true ipark-drawing comment --account 1

# 약속 시간 자동 대기 (10초 전 페이지 진입 → 90초 윈도우 동안 reload 폴링)
HEADFUL=true ipark-drawing comment --account 1 --at 10:00:00
```

테스트 카페 게시글의 댓글창이 막혀 있으면 매 5초마다 reload하면서 차단 해제를 감지합니다. 90초 안에 안 열리면 "비활성 — 추첨 없음"으로 정상 종료.

### 5) 운영용 명령

```sh
# 목요일 10:00 — 3계정 병렬 댓글 + 텔레그램 알림 (1차)
# article-id 미지정 시 자동 탐색
ipark-drawing run-morning --accounts 1,2,3 --at 10:00:00

# 목요일 14:30 — 당첨자 확인 + 텔레그램 알림 (2차)
ipark-drawing check-winners --account 1 --result-url "<발표 URL>"

# 수요일 — '공실 안내' 글 단발 조회 (폴링은 scripts/run-notice.sh가 담당)
ipark-drawing check-notice --account 1
```

`--no-notify`로 텔레그램 끄고 디버깅 가능.

### 6) launchd로 자동화

`scripts/install-launchd.sh`로 4개 잡 한 번에 등록.

```sh
./scripts/install-launchd.sh
launchctl list | grep ipark-drawing                       # 4개 잡 확인
launchctl print gui/$(id -u)/com.ipark-drawing.morning    # runs / last exit code
tail -f data/morning.out.log data/morning.err.log
```

| 잡 | Weekday | 트리거 | 역할 |
|----|---------|------|------|
| `notice` | 수 (3) | 10:00 → 12:00 (30분 간격 5회) | '공실 안내' 글 발견 시 즉시 텔레그램 / 12:00까지 미발견 시 "직접 확인" 알림 |
| `heartbeat` | 목 (4) | 09:55 | morning 직전 sanity ping (env·텔레그램 검증) |
| `morning` | 목 (4) | 09:57 | discover → 10:00 댓글 작성 → 1차 알림 |
| `winners` | 목 (4) | 14:30 → 15:00 (5분 간격 7회) | discover 폴링 → 발견 시 매치 + 2차 알림 / 미발견 시 "직접 확인" 알림 |

매주 게시글 ID는 자동 탐색됩니다.

### 운영 안정성 가이드

- **노트북 sleep 방지**: launchd는 sleep 중인 맥북을 깨우지 않습니다. 시스템 환경설정 → 배터리 → "디스플레이가 꺼졌을 때 컴퓨터가 자동으로 잠자기 못함" 또는 트리거 직전에 `caffeinate -i &` 실행 권장. 노트북을 항상 켜두는 환경이라면 무시해도 OK.
- **state 파일** `data/state/last-run.json`이 두 잡 사이의 분기 신호입니다:
  - 오전에 추첨이 안 열렸으면 winners 잡은 silent skip
  - 같은 날 winners 잡이 두 번 트리거되면 두 번째는 dedup
- **로그 위치**: `data/{notice,heartbeat,morning,winners}.{out,err}.log`
- **쿠키 만료**: 댓글 흐름에서 `LOGIN_EXPIRED` 감지되면 텔레그램으로 즉시 알림이 갑니다. 그때 `ipark-drawing login --account N`으로 갱신.
- **로그·스크린샷 정리**: `./scripts/cleanup-old.sh`로 30일 경과 파일 + 10MB 초과 로그 삭제. 필요 시 별도 launchd 잡으로 묶어 주기 실행 가능.

### Troubleshooting FAQ

**Q1. "잠시 후 다시 확인해주세요" 차단 페이지가 떠요.**
네이버가 자동화를 의심한 상태입니다. 일반 브라우저에서 같은 URL이 잘 열리는지 먼저 확인 → 정상이면 우리 봇 시그니처만 차단된 것이므로 30분 정도 대기 후 재시도. 자주 발생하면 `patchright`를 최신 버전으로 업그레이드.

**Q2. 텔레그램 메시지가 도착하지 않아요.**
1) `.env`의 `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` 점검
2) 봇이 채팅방 멤버인지 확인 (BotFather에서 발급한 봇을 새 채팅방에 초대 필요)
3) `data/morning.err.log`에서 `Telegram 알림 발송 실패` 키워드 검색

**Q3. 목요일 10시인데 launchd가 안 돌았어요.**
- 노트북이 sleep이면 trigger 누락. 환경설정 → 배터리에서 절전 비활성화하거나 09:50쯤 깨워두기.
- `launchctl list | grep ipark-drawing`로 4개 잡 등록 상태 확인.
- `launchctl print gui/$(id -u)/com.ipark-drawing.morning` → `runs`가 0이면 한 번도 트리거되지 않음. 오늘이 목요일이 아니면 정상이고, 목요일이라면 sleep 또는 등록 누락.
- `data/morning.{out,err}.log` 비어 있으면 launchd 자체가 안 돈 것 → `./scripts/install-launchd.sh` 재실행.

**Q4. 시간대가 어긋나요 (10시인데 11시에 돌거나).**
macOS 시간대를 `Asia/Seoul`로 설정. 시스템 환경설정 → 일반 → 날짜 및 시간 → 시간대.

**Q5. 똑같은 알림이 두 번 와요.**
launchd 재로드 직후 한 번 일어날 수 있는 race condition입니다. `data/state/last-run.json`의 `morning_notified`/`winners_notified` 플래그가 dedup 처리. 그래도 두 번 오면 launchd 잡이 중복 등록된 건지 `launchctl list | grep ipark-drawing`로 확인.

**Q6. 캡챠가 자주 떠요.**
첫 로그인은 사용자가 직접 했어도, 이후 자동화가 의심받을 수 있습니다.
1) 같은 노트북·같은 IP·같은 Wi-Fi 환경 유지
2) 너무 자주 수동 테스트하지 않기
3) 한 번 캡챠가 트리거된 계정은 30분~수시간 휴식 후 재시도

**Q7. 발표 글이 게시됐는데 매치를 못 잡아요.**
1) 발표 형식이 우리가 가정한 표 구조 (`타입/층수/보증금/임대료`)와 다를 수 있음 → `find_winners`가 fallback으로 body 텍스트 매치 사용
2) 마스킹 글자가 `*` 외 다른 기호일 수 있음 → `winner_check.py`의 `_MASK_CHARS`에 추가
3) `data/state/history/<date>.json`로 그날 결과 확인 가능

## 구조

```
src/ipark_drawing/
├── config.py         # 환경변수 / 계정 / selector
├── browser.py        # patchright(stealth fork) 컨텍스트
├── naver_auth.py     # 쿠키 기반 로그인 검증
├── board_finder.py   # 전체글 게시판에서 오늘 글 자동 탐색 (kind=comment|winner|notice)
├── comment_bot.py    # reload 폴링 + 댓글 작성 + 카운터 검증
├── orchestrator.py   # 3계정 병렬 실행 + 텔레그램 1차 알림
├── winner_check.py   # 발표 게시글 텍스트에서 우리 댓글 매치
├── notice_check.py   # 수요일 공실 안내 글 파싱 + 메시지 포맷
├── heartbeat.py      # morning 직전 sanity ping
├── state.py          # last-run.json + history 스냅샷
├── locking.py        # 잡 동시 실행 방지 락
├── telegram.py       # Bot API HTTP, chat_id 자동 변환
├── inspector.py      # selector 디버깅용 DOM 덤프
└── cli.py            # CLI 엔트리포인트

scripts/
├── com.ipark-drawing.notice.plist    # 수 10:00
├── com.ipark-drawing.heartbeat.plist # 목 09:55
├── com.ipark-drawing.morning.plist   # 목 09:57
├── com.ipark-drawing.winners.plist   # 목 14:30
├── run-notice.sh                     # check-notice 폴링 래퍼 (5회/30분 간격)
├── run-morning.sh                    # discover + run-morning 래퍼
├── run-winners.sh                    # discover 폴링 + check-winners 래퍼
├── install-launchd.sh                # 4개 잡 한 번에 등록
└── cleanup-old.sh                    # 30일 경과 파일 + 10MB 초과 로그 정리
```

## 테스트

```sh
pytest
```

브라우저 통합 테스트는 별도. 위 명령은 mock 기반 단위 테스트만 돌립니다.

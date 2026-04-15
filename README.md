# HT 운동 인증 카운터

카카오톡 [#HT] 인증방에 올라온 운동 인증 사진을 매일 자동으로 집계하고, 운영진방에 결과를 전달하는 macOS 전용 봇.

- 카카오톡 Mac 로컬 DB에서 사진 메시지 수집 (`kakaocli`)
- Python으로 파싱·집계·SQLite 저장
- 카카오톡 Accessibility API로 운영진방에 메시지 전송 (`kmsg`)
- launchd로 매일 23:00 KST 자동 실행

## 아키텍처

```
┌────────────┐   kakaocli messages   ┌───────────┐
│ KakaoTalk  │ ───────────────────▶ │ collector │
│ Mac 로컬DB │                       └─────┬─────┘
└────────────┘                             │ JSON
                                           ▼
                                     ┌───────────┐
                                     │  counter  │  photo + 일별 캡 + 중복플래그
                                     └─────┬─────┘
                                           ▼
                                     ┌───────────┐
                                     │  storage  │  data/counter.db (verifications, summaries, run_log)
                                     └─────┬─────┘
                                           ▼
                                     ┌───────────┐   kmsg send --chat-id
                                     │  notifier │ ─────────────────▶ [#HT] 운영진방
                                     └───────────┘
```

## 요구 사항

- **macOS** (Accessibility API 필수, 다른 OS에서는 동작 안 함)
- **KakaoTalk Mac 앱** 로그인 + 대상 오픈채팅방 참여
- **Python 3.9+** (시스템 python3 사용 가능)
- **Homebrew**
- **[kakaocli](https://github.com/kusin14/kakaocli)** — 로컬 DB 읽기 + SQL 쿼리 (집계용)
- **[kmsg](https://github.com/channprj/kmsg)** — 카카오톡 AX 자동화 송신 (전달용)
- macOS 접근성 권한 (KakaoTalk, 터미널/Python, kmsg)

## 설치

### 1. Homebrew 도구 설치

```bash
# kakaocli
brew tap kusin14/kakaocli
brew install kakaocli

# kmsg
brew tap channprj/tap
brew install kmsg
```

### 2. macOS 접근성 권한 부여

`System Settings → Privacy & Security → Accessibility`에서 아래 앱에 권한을 부여:

- **KakaoTalk**
- **터미널** (iTerm2 / Terminal.app — cron/launchd 실행 주체)
- **python3** (선택, 직접 실행할 때)

확인:

```bash
kmsg status
# Accessibility permission: granted 이면 OK
```

### 3. 프로젝트 클론 및 설정

```bash
git clone https://github.com/<USERNAME>/ht-exercise-counter.git
cd ht-exercise-counter

# 예시 설정 복사
cp config.example.json config.json
```

`config.json` 편집. `chat_id` / `admin_chat_id` / `members`를 실제 값으로 바꿔야 함 (아래 [config.json 설정](#configjson-설정) 참고).

### 4. 동작 확인 (dry-run)

```bash
python3 main.py
```

`dry_run: true` 상태이므로 실제 전송되지 않고 `logs/counter.log`와 화면에 `[DRY RUN] → chat_XXXX: ...` 형태의 프리뷰만 출력됨.

### 5. launchd 자동화 설치

```bash
./install-launchd.sh
```

- 템플릿 `launchd/com.ht.exercise-counter.plist.template`를 현재 경로/python으로 치환
- `~/Library/LaunchAgents/com.ht.exercise-counter.plist`에 복사
- `launchctl load` 실행
- 매일 **23:00 KST**에 `main.py` 실행

확인:

```bash
launchctl list | grep com.ht.exercise-counter
```

해제:

```bash
launchctl unload ~/Library/LaunchAgents/com.ht.exercise-counter.plist
```

## config.json 설정

| 키 | 설명 | 예시 |
|---|---|---|
| `chat_name` | 수집 대상 채팅방 이름 (표시용) | `"[#HT] 인증방"` |
| `chat_id` | kakaocli 정수형 chat_id (`kakaocli chats`로 조회) | `123456789012345` |
| `admin_chat_id` | kmsg hash chat_id (`kmsg chats --json`로 조회) | `"chat_XXXXXXXXXXXX"` |
| `admin_chat_name` | 전송 대상 이름 (로그/표시용) | `"[#HT] 운영진방"` |
| `admin_sender` | 송신 백엔드 — 현재 `"kmsg"`만 지원 | `"kmsg"` |
| `photo_message_type` | kakaocli JSON의 사진 타입 라벨 | `"photo"` |
| `photo_message_type_raw` | NTChatMessage.type 원시 정수값 | `2` |
| `weekly_target` | 주간 인증 목표 횟수 | `4` |
| `week_start_day` | 주 시작 요일 | `"monday"` |
| `timezone` | 타임존 (KST 고정) | `"Asia/Seoul"` |
| `duplicate_window_minutes` | 같은 멤버 연속 업로드를 flagged로 표시할 간격 | `3` |
| `daily_cap_per_member` | 멤버당 하루 최대 집계 횟수 | `1` |
| `db_path` | SQLite 파일 경로 (상대/절대) | `"data/counter.db"` |
| `dry_run` | `true`면 실제 전송 없이 로그만 | `true` |
| `members` | 멤버 배열 — 아래 구조 | (다음 참고) |

### 멤버 구조

```json
{
  "user_id": 1111111,
  "canonical": "홍길동",
  "aliases": ["홍길동", "홍길동.90", "홍"]
}
```

- `user_id`: kakaocli가 반환하는 정수 user_id
- `canonical`: 리포트에 표시될 정규명
- `aliases`: 동일인의 모든 표기 (정규화 용도)

### 메시지 템플릿 커스터마이징

운영진방에 전송되는 문구는 `templates/` 디렉토리의 **.md 파일**로 관리한다. 코드나 JSON을 건드릴 필요 없이 텍스트 파일을 편집하듯 자유롭게 바꿀 수 있다.

```
templates/
├── daily.md   # 일별 집계 리포트
├── weekly.md  # 주간 요약 리포트 (일요일만)
└── error.md   # 수집/시스템 오류 알림
```

`templates/daily.md`:

```md
[HT 인증 집계] {date}
총 인증: {capped_count}건{multi_upload_block}{member_list_block}

— AI 자동 송신 (HT 운동 인증 카운터)
```

`templates/weekly.md`:

```md
[HT 주간 인증 요약] {week_key}{member_details_block}

— AI 자동 송신 (HT 운동 인증 카운터)
```

`templates/error.md`:

```md
[HT 인증 시스템 오류]
{error_msg}
수동 확인이 필요합니다.

— AI 자동 송신 (HT 운동 인증 카운터)
```

#### 사용 가능한 플레이스홀더

| 템플릿 | 키 | 설명 |
|---|---|---|
| `daily.md` | `{date}` | 집계 날짜 (예: `2026-04-15`) |
| `daily.md` | `{capped_count}` | 총 인증 건수 (일일 캡 적용) |
| `daily.md` | `{multi_upload_block}` | 2회 이상 올린 사람만 `[다회 업로드]` 섹션으로 (없으면 빈 문자열) |
| `daily.md` | `{member_list_block}` | 인증자 이름을 쉼표로 나열 (`"인증자: 홍길동, 김철수, ..."`) |
| `weekly.md` | `{week_key}` | 주 식별자 (ISO 형식: `2026-W16`) |
| `weekly.md` | `{member_details_block}` | 모든 멤버 × (횟수 + 각 인증 timestamp 시:분). 횟수 내림차순 정렬. |
| `error.md` | `{error_msg}` | 에러 본문 |

#### 출력 예시

**Daily (평상시)**

```
[HT 인증 집계] 2026-04-15
총 인증: 55건

인증자: 김철수, 박영희, 이영수, ..., 홍길동

— AI 자동 송신 (HT 운동 인증 카운터)
```

**Daily (누군가 하루에 여러 번 올린 경우)**

```
[HT 인증 집계] 2026-04-15
총 인증: 55건

[다회 업로드]
  홍길동: 3회
  김철수: 2회

인증자: 박영희, ..., 김철수, ..., 홍길동, ...

— AI 자동 송신 (HT 운동 인증 카운터)
```

**Weekly**

```
[HT 주간 인증 요약] 2026-W16

김철수: 4회
  - 04-13 월 07:23
  - 04-14 화 07:45
  - 04-15 수 07:30
  - 04-16 목 08:12

홍길동: 2회
  - 04-13 월 18:05
  - 04-15 수 19:22

박영희: 0회

— AI 자동 송신 (HT 운동 인증 카운터)
```

timestamp는 `"MM-DD 요일 HH:MM"` 포맷(KST)으로 자동 변환된다. 반복 행 스타일이나 포맷을 바꾸고 싶으면 `notifier.py`의 `_render_*_block` / `_format_ts` 함수를 수정.

템플릿 경로는 `config.templates_dir`로 변경 가능 (기본값: `templates`).

### 멤버 목록 자동 동기화

`members` 배열을 수동으로 관리할 필요 없다. `sync_members.py`가 카카오톡 로컬 DB에서 해당 인증방에 메시지를 보낸 적 있는 모든 유저를 추출해 기존 엔트리와 병합한다:

```bash
# 변경 사항 미리보기 (config.json 수정하지 않음)
python3 sync_members.py --dry-run

# 실제 반영 (config.json.bak 자동 백업)
python3 sync_members.py

# 채팅방에서 사라진 멤버 제거까지 반영
python3 sync_members.py --prune
```

동작:
- 기존 `canonical` / `aliases`는 그대로 보존
- 현재 DB의 `displayName`이 `aliases`에 없으면 추가
- 신규 `user_id`는 새 엔트리로 생성 (canonical = 닉네임에서 `.연도` 접미사 제거)
- 기본 동작은 사라진 멤버를 **보존** (과거 집계 이력 보호). 정리하려면 `--prune`.

내부 쿼리는 `NTChatMessage` + `NTMultiProfile`(linkId=0) + `NTUser` LEFT JOIN. 잠수 중인(메시지 기록이 전혀 없는) 멤버는 이 방법으로 잡히지 않지만, 인증 집계 목적에서는 메시지 이력이 있는 유저만 대상이므로 충분.

### chat_id 수동 조회 방법

```bash
# kakaocli 정수 chat_id 조회 (수집용)
kakaocli chats | grep HT

# 또는 DB 직접 쿼리
kakaocli query "SELECT chatId, chatName FROM NTChatRoom WHERE chatName LIKE '%HT%' OR extra LIKE '%HT%'"

# kmsg hash chat_id 조회 (전송용)
kmsg chats --json | python3 -m json.tool | grep -B1 '운영진'
```

## 파일 구조

```
ht-exercise-counter/
├── main.py                                      # 오케스트레이터 (수집→집계→저장→전송)
├── collector.py                                 # kakaocli 호출 및 JSON 파싱
├── counter.py                                   # 사진 필터링, 일별/주간 집계, 중복플래그
├── storage.py                                   # SQLite CRUD, 멱등성 (msg_hash UNIQUE)
├── notifier.py                                  # kmsg send 래퍼 (운영진방 전송)
├── sync_members.py                              # members 배열 자동 동기화 (kakaocli DB 기반)
├── config.example.json                          # 설정 템플릿
├── config.json                                  # 실사용 설정 (.gitignore)
├── templates/                                   # 메시지 템플릿 (md 파일로 관리)
│   ├── daily.md                                 # 일별 집계 포맷
│   ├── weekly.md                                # 주간 요약 포맷
│   └── error.md                                 # 에러 알림 포맷
├── install-launchd.sh                           # launchd 등록 스크립트
├── launchd/
│   └── com.ht.exercise-counter.plist.template   # launchd 템플릿 (플레이스홀더 포함)
├── data/                                        # SQLite DB (.gitignore)
├── logs/                                        # 실행 로그 (.gitignore)
├── README.md
└── DEVELOPMENT_LOG.md                           # 주요 이슈 해결 이력
```

## 동작 규칙

- **타임존 고정**: 모든 시간은 `Asia/Seoul` 기준
- **사진 인증 정의**: `NTChatMessage.type == 2` (kakaocli JSON 라벨 `"photo"`)
- **일일 캡**: 멤버당 하루 최대 `daily_cap_per_member`번만 카운트
- **주 시작**: 월요일 00:00 KST (`%G-W%V`)
- **멱등성**: `msg_hash = sha256(sender_id|timestamp|type)`가 UNIQUE
- **0건 처리**: 수집 결과 0건이면 정상 집계로 저장하지 않고 에러 알림 발송
- **dry-run**: `config.dry_run=true`면 `[DRY RUN]` 로그만 남김

## 트러블슈팅

### `kakaocli: command not found`

```bash
which kakaocli
# 없으면
brew tap kusin14/kakaocli && brew install kakaocli
```

launchd에서 실행될 땐 `EnvironmentVariables.PATH`가 `/usr/local/bin:/opt/homebrew/bin` 모두 포함해야 한다. plist 템플릿에 이미 반영돼 있음.

### `kmsg send: rc=1` 또는 "Chat not found" 가 연속 전송 때만 발생

첫 번째 전송은 성공했는데 두 번째부터 실패한다면, 카카오톡 사이드바가 '친구' 또는 '오픈채팅' 탭으로 리셋된 상태다. kmsg는 **현재 활성 사이드바 탭 안에서만** 채팅방을 검색하므로, '채팅' 탭이 아니면 찾지 못한다.

`notifier._prepare_kakaotalk()`이 매 전송 직전에 자동으로:
1. KakaoTalk `activate`
2. "카카오톡" 메인 창 `AXRaise`
3. `Cmd+2` 단축키로 '채팅' 탭 강제 전환
4. 0.4초 delay

를 수행한다. 이 경로가 막혀 있는지 수동 재현:

```bash
osascript -e 'tell application "KakaoTalk" to activate'
osascript -e 'tell application "System Events" to tell process "KakaoTalk" to perform action "AXRaise" of (first window whose name is "카카오톡")'
osascript -e 'tell application "System Events" to tell process "KakaoTalk" to keystroke "2" using command down'
# 이 상태에서 kmsg send가 되는지 확인
```

KakaoTalk 단축키: `Cmd+1`(친구) / `Cmd+2`(채팅) / `Cmd+3`(오픈채팅). 다른 앱에서 이 단축키가 가로채고 있지 않은지도 확인.

### `kmsg send: WINDOW_NOT_READY`

카카오톡 메인 윈도우(`title == "카카오톡"`)가 현재 앞에 있지 않거나 서브 채팅창이 raise된 상태. `notifier._raise_main_window()`가 osascript로 메인 윈도우를 강제 raise한다. 그래도 실패하면:

```bash
# 카카오톡 수동 실행 + 메인 창 활성화 후 재시도
open -a KakaoTalk
osascript -e 'tell application "System Events" to tell process "KakaoTalk" to perform action "AXRaise" of (first window whose name is "카카오톡")'
kmsg send --chat-id "chat_XXXXXXXXXXXX" "test"
```

### `kmsg send: Chat not found` 또는 셀프챗 실패

- kmsg는 **셀프챗(본인과의 대화방)** 에 메시지를 보내지 못한다. 오픈채팅/1:1/그룹만 동작.
- 테스트는 본인이 혼자 있는 더미 오픈채팅방을 하나 만들어서 `admin_chat_id`에 세팅.

### 집계가 0건으로 나오는데 실제 사진은 있음

- **자정 직후 실행** 문제: `kakaocli messages --since 1d`는 24시간 window라 어제 사진만 잡히는데 `counter.count_verifications`는 기본적으로 "오늘 날짜"로 필터링 → 0건
- 해결: `counter.count_verifications(messages, config, target_date="YYYY-MM-DD")`에 명시적으로 어제 날짜 전달, 또는 `collector.collect_messages(..., since="2d")`로 수집 범위 확대
- launchd는 23:00 KST에 실행되므로 평상시엔 이 문제 없음

### 한 명이 "2회"로 중복 집계됨

- 원인: `--since 1d`는 정확히 24시간이라 어제 23:XX 사진까지 함께 수집됨 → `counter`가 고유 날짜 수를 세면 날짜가 2개가 되어 2로 카운트
- 해결: `target_date` 파라미터 명시 (현재 main.py는 오늘 날짜로 고정 — launchd 실행 시간인 23:00 KST에서는 거의 문제되지 않음)

### KakaoTalk 앱이 꺼져 있음

`main.py`의 `ensure_kakaotalk_running()`이 자동으로 `open -a KakaoTalk` 실행 + 10초 대기. 단 Mac이 슬립 상태라면 launchd가 깨우지 않으므로, 사무실 상주 Mac을 권장.

### Python 3.9 호환성

모든 모듈 상단에 `from __future__ import annotations`가 있어야 `int | None` 같은 PEP 604 문법이 런타임 평가에서 실패하지 않음. 이미 반영돼 있음.

## 개발 로그

주요 이슈 해결 이력(특히 kakaocli send → kmsg 전환, 타임존 경계 이슈 등)은 [DEVELOPMENT_LOG.md](DEVELOPMENT_LOG.md)에서 자세히 확인.

## 라이선스

MIT

## 면책

본 도구는 카카오톡 Mac 앱의 로컬 SQLite DB를 읽어 통계 용도로만 사용합니다. 수집된 데이터는 로컬에만 저장되며, 수집 대상 채팅방의 참여자 동의 하에 운영하시기 바랍니다.

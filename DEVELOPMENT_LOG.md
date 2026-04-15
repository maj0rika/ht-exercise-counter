# 개발 로그

프로젝트 구현 과정에서 실제로 마주친 이슈와 해결 방법 기록. 같은 문제가 재발하거나 포크 사용자가 참고할 수 있도록 정리.

---

## 타임라인 요약

1. **초기 셋업** — kakaocli로 메시지 수집 / 사진 필터 / SQLite 저장 골격 작성
2. **PRD 4개 스토리 완료** — DB 스키마 검증, 실제 멤버 목록 반영, dry-run 전체 흐름, launchd plist
3. **운영진방 실전 전송 요청** — "kakaocli send가 안 되는데?"
4. **송신 백엔드 교체 여정** — `kakaocli send` → 실패 → 여러 AX API 직접 제어 시도 → `kmsg` 발견 → 최종 도입
5. **타임존 경계 버그** — 자정 직후 집계 0건 문제
6. **실전 전송 성공** — 운영진방에 실제 리포트 송신 완료

---

## 이슈 1. kakaocli send가 `Chat not found`로 실패

### 증상

```bash
kakaocli send "[#HT] 운영진방" "test"
# Error: Chat not found
```

멤버 목록 조회, 메시지 수집, SQL 쿼리는 모두 정상 동작. 송신만 실패.

### 원인

- `kakaocli`의 `send` 서브커맨드는 Mac 앱의 입력 UI를 제어해야 하는데, 오픈채팅방 타이틀 매칭이 최근 버전에서 깨짐
- CLI가 chat 객체를 내부 목록에서 찾지 못하고 "Chat not found" 반환

### 해결 — 송신 백엔드를 `kmsg`로 교체

`kmsg`는 macOS Accessibility API로 카카오톡을 직접 조작하는 별도 CLI.

```bash
brew tap channprj/tap
brew install kmsg
kmsg status   # Accessibility: granted
kmsg chats --json   # hash 형태 chat_id 목록
```

`notifier.py`를 `kakaocli send` → `kmsg send --chat-id <chat_XXXX>`로 교체.

- `config.json.admin_chat_id`: 기존 `int (user_id)` → kmsg의 `string (chat_XXXX 해시)`로 변경
- `_send()`가 `chat_target.startswith("chat_")`이면 `--chat-id` 플래그 사용, 아니면 채팅방 이름 fuzzy-match

### 교훈

- 카카오톡 Mac 앱 AX 자동화는 `kakaocli`보다 `kmsg`가 훨씬 안정적
- kakaocli는 "DB 읽기 / SQL 쿼리" 전문, kmsg는 "AX 송신 / 읽기 / 자동화" 전문 → 역할 분리
- Homebrew tap이 공개되어 있으므로 설치만 간단하면 의존 추가 부담 낮음

---

## 이슈 2. `kmsg send`가 `WINDOW_NOT_READY`로 실패

### 증상

kmsg 설치 후 첫 전송 시도:

```
kmsg send --chat-id chat_XXXX "test"
# Error: WINDOW_NOT_READY
```

### 원인 파악 과정

1. `kmsg inspect`로 AX 트리를 찍어 봄
2. `kmsg`는 `windows[0]`을 기본 대상으로 검사하는데, KakaoTalk 프로세스에 여러 윈도우가 있을 때 `windows[0]`가 **메인 "카카오톡" 윈도우가 아닌 개별 채팅창**(별도 오픈채팅방 제목)일 수 있음
3. 개별 채팅창에는 리스트/검색 패널이 없어 `kmsg`가 송신 대상 선택 UI를 찾지 못함 → `WINDOW_NOT_READY`

### 해결 — 송신 전 "카카오톡" 메인 윈도우 강제 raise

```python
RAISE_MAIN_WINDOW_SCRIPT = (
    'tell application "System Events" to tell process "KakaoTalk" '
    'to perform action "AXRaise" of (first window whose name is "카카오톡")'
)

def _raise_main_window():
    subprocess.run(["osascript", "-e", RAISE_MAIN_WINDOW_SCRIPT], ...)
```

- `tell application "KakaoTalk" to activate` 만으로는 부족 (잘못된 서브윈도우가 올라올 수 있음)
- `AXRaise` + `first window whose name is "카카오톡"` 조합으로 메인 윈도우를 명시적으로 지목해야 안정적

### 검증

- Raise 후 `kmsg inspect` 다시 실행 → `Inspecting window: 카카오톡 [role: AXWindow, title: "카카오톡"]` 확인
- `kmsg send --chat-id chat_XXXXXXXXXXXX "test"` 성공

### 교훈

- macOS AX API는 "앞에 보이는 윈도우"가 곧 `windows[0]`이라는 가정을 자주 한다
- 멀티 윈도우 앱을 자동화할 땐 **제목으로 타겟팅 후 raise**하는 루틴을 송신 직전에 항상 삽입

---

## 이슈. 수동 테스트 중 macOS Funk 경고음 폭주 (굉음)

### 증상

수동으로 여러 건 연속 전송 스크립트를 돌렸을 때, 맥북 스피커에서 시스템 경고음이 빠르게 반복되며 사용자가 "굉음"이라고 표현할 정도로 크게 울렸다. 동시에 kmsg는 `rc=1`로 첫 번째 전송부터 실패.

진단 시점:
- `osascript`로 확인한 frontmost 앱: `Electron` (Claude Code 본체)
- 시스템 볼륨: 63/100
- 백그라운드 전송 스크립트 출력: `kmsg send 실패 (rc=1)` 첫 줄부터

### 원인

`_prepare_kakaotalk`이 매 `_send` 호출 직전에 실행하던 세 AppleScript:

```applescript
tell application "KakaoTalk" to activate
tell application "System Events" to tell process "KakaoTalk" to perform action "AXRaise" of ...
tell application "System Events" to tell process "KakaoTalk" to keystroke "2" using command down
```

문제는 **세 번째 줄**. `tell process "KakaoTalk" to keystroke ...` 구문에도 불구하고 System Events의 `keystroke`는 실제로 **OS 전역 입력 스트림**을 통해 키를 쏘기 때문에, 진짜 frontmost 앱이 키를 받는다.

macOS는 보안상 `activate`만으로는 **다른 앱의 인풋 포커스를 자동으로 넘겨주지 않는다**. 사용자가 Claude Code(Electron)를 계속 보고 있는 동안 카톡은 `activate`만 받고 실제 frontmost는 Electron 그대로. 그 상태에서 `keystroke "2" using command down`이 날아가면:

1. Electron(Claude Code)이 Cmd+2를 받음
2. Claude Code가 이 단축키를 처리 못 해 **시스템 Funk 알림음**(`/System/Library/Sounds/Funk.aiff`) 재생
3. `_prepare_kakaotalk`가 매 전송마다 3번씩 osascript를 호출하는데, 이 중 AXRaise / keystroke도 실패하면 각각 bonk → 3건 시도 × 여러 번 = 10회 이상 경고음이 0.2~0.4초 간격으로 반복
4. 볼륨 63 환경에서 굉음처럼 들림
5. 카톡은 포커스를 못 잡아 `kmsg send`도 `rc=1`로 실패

### 해결

`_prepare_kakaotalk`에 **frontmost 가드** 추가:

```python
def _is_kakaotalk_frontmost() -> bool:
    r = subprocess.run(["osascript", "-e",
        'tell application "System Events" to get name of first process whose frontmost is true'],
        capture_output=True, text=True, timeout=3)
    return "KakaoTalk" in (r.stdout or "")

def _prepare_kakaotalk():
    subprocess.run(["osascript", "-e", 'tell application "KakaoTalk" to activate'], ...)
    for _ in range(15):
        if _is_kakaotalk_frontmost():
            break
        time.sleep(0.2)
    else:
        logger.warning("카카오톡이 frontmost가 아님 — keystroke 생략")
        return
    # 이제 카톡이 확실히 frontmost이므로 AXRaise / Cmd+2 안전
    ...
```

추가로 `kmsg send`에 `--deep-recovery` 플래그를 붙여, 탭 전환 keystroke를 생략한 경우에도 kmsg 자체가 윈도우 복구를 시도하도록 한다.

```python
base = ["kmsg", "send", "--keep-window", "--deep-recovery"]
```

### 검증

- 카톡을 앞에 띄우지 않은 상태에서 전송 시도: `"카카오톡이 frontmost가 아님 — keystroke 생략, kmsg --deep-recovery로 복구 시도"` 경고 + keystroke 전혀 안 날아감 → 엉뚱한 앱에 Cmd+2 누설 없음 → Funk 경고음 없음
- 사용자가 직접 카톡을 앞에 띄운 뒤 실행: 기존처럼 AXRaise + Cmd+2 정상 동작
- launchd 23:00 백그라운드 실행: 다른 앱이 frontmost가 아니므로 원래 이 문제 영향권 밖

### 교훈

- `tell process "X" to keystroke ...`는 **타겟팅이 아니라 힌트**에 가깝다. 실제 키 전달은 OS 전역 입력 큐를 거치므로 현재 frontmost가 받는다.
- 자동화로 앱을 "앞으로 올리는" 것과 "인풋 포커스를 뺏는" 것은 macOS에서 별개. `activate`는 전자만 보장한다.
- 수동 테스트 스크립트가 호출하는 UI 조작 명령은 **항상 frontmost 확인 후 실행** 해야 엉뚱한 앱으로 키 누설 안 됨.
- 시스템 볼륨이 높을 때 bonk 반복은 "굉음"으로 체감될 수 있다. 자동화 도구가 내는 비명은 곧 "내가 지금 UI를 잘못 건드리고 있다"는 신호다.

---

## 이슈 3. 연속 전송 시 2번째부터 `kmsg send` 실패 — 사이드바 탭 문제

### 증상

4개 리포트(daily 평상시, daily 다회 업로드, weekly, empty daily)를 연속 전송하는 통합 테스트에서 **첫 번째만 성공하고 2~4번째가 모두 `kmsg send 실패 (rc=1)`** 로 끝남. 

```
2026-04-16 01:27:13,930 [INFO] 전송 완료 → chat_XXXXXXXXXXXX
2026-04-16 01:27:21,042 [ERROR] kmsg send 실패 (rc=1): 
2026-04-16 01:27:26,703 [ERROR] kmsg send 실패 (rc=1): 
2026-04-16 01:27:32,167 [ERROR] kmsg send 실패 (rc=1): 
```

### 원인

카카오톡 UI를 직접 확인한 결과, 사이드바가 **'친구' 탭**으로 리셋되어 있었다. kmsg는 채팅방을 검색할 때 **현재 활성화된 사이드바 탭 내부에서만** 목록을 순회하기 때문에, 친구 탭이 활성이면 채팅방 검색이 실패한다.

- 첫 번째 전송은 수동으로 카톡을 열어 둔 시점의 채팅 탭 상태 덕분에 성공
- 이후 `kmsg send`가 창을 닫거나 다른 탭으로 돌아가면 다음 전송부터 친구 탭에서 검색
- osascript `AXRaise`만으로는 탭 상태가 바뀌지 않는다

### 해결 — `_prepare_kakaotalk`에서 매번 Cmd+2로 '채팅' 탭 강제 전환

`notifier.py`:

```python
SWITCH_TO_CHAT_TAB_SCRIPT = (
    'tell application "System Events" to tell process "KakaoTalk" '
    'to keystroke "2" using command down'
)

def _prepare_kakaotalk():
    subprocess.run(["osascript", "-e", 'tell application "KakaoTalk" to activate'], ...)
    subprocess.run(["osascript", "-e", RAISE_MAIN_WINDOW_SCRIPT], ...)
    subprocess.run(["osascript", "-e", SWITCH_TO_CHAT_TAB_SCRIPT], ...)
    time.sleep(0.4)
```

카카오톡 Mac의 단축키는:
- `Cmd+1` → 친구
- `Cmd+2` → 채팅  ← kmsg가 필요로 하는 탭
- `Cmd+3` → 오픈채팅

매 `_send` 호출 직전에 이 시퀀스를 실행해서, 이전 상태와 상관없이 "채팅 탭이 활성화된 메인 창"을 보장한다.

### 교훈

- AX 자동화 도구는 "앱이 떠 있다" ≠ "올바른 UI 상태다"
- 멀티 탭 UI를 자동화할 땐 탭 상태까지 **매 호출마다 강제 복원** 해야 안정적
- 연속 전송 테스트는 배치로 돌려야 이런 상태 누수를 잡을 수 있다 (단일 전송만 테스트하면 놓침)

---

## 이슈 4. `kmsg`가 셀프챗을 열지 못함

### 증상

테스트 목적으로 본인과의 대화(셀프챗)를 `admin_chat_id`로 쓰려고 했는데 `WINDOW_NOT_READY`가 지속.

### 원인

- 카카오톡 셀프챗은 일반 채팅방 목록에 노출되지만 AX 트리상 다른 경로로 열림
- `kmsg`가 AX 패턴으로 인식하지 못함 (업스트림 한계로 보임)

### 해결 — 셀프챗 대신 실사용 채팅방을 대상으로 지정

- 어차피 실제 타겟은 [#HT] 운영진방이므로 셀프챗은 테스트 목적이었을 뿐
- 운영진방으로 직접 테스트 전송 (내용에 "[테스트] ... 무시하셔도 됩니다" 명시)
- 실전 환경에서 첫 전송 리허설까지 함께 겸함

### 교훈

- 테스트 전용 더미 채팅방을 실제 앱 레벨에서 하나 파 두는 게 안전
- 셀프챗은 자동화 대상이 아님을 전제

---

## 이슈 4. 자정 직후 dry-run이 0건으로 나옴

### 증상

```bash
python3 main.py
# 수집된 메시지: 58건
# 집계 결과: capped=0, raw_photo=0
# [DRY RUN] → chat_XXXX: 총 인증: 0건
```

58건이 수집됐는데 집계는 0건. 데이터가 분명히 있음에도 빈 리포트.

### 원인

- 실행 시각: 2026-04-16 00:48 KST (자정 직후)
- `kakaocli messages --since 1d`는 정확히 24시간 전부터 현재까지의 메시지를 가져옴 → 2026-04-15 00:48 ~ 04-16 00:48
- `counter.count_verifications`는 기본 `target_date = datetime.now(KST).strftime("%Y-%m-%d")` = "2026-04-16"
- 수집된 58건의 사진은 모두 `timestamp = 2026-04-15T14:10:14Z` (= 04-15 23:10 KST) 같은 어제 데이터
- 타임스탬프를 KST로 변환해 `2026-04-16`과 비교하면 매칭 0건 → `todays_msgs = []`

### 해결

- **프로덕션 환경**: launchd가 23:00 KST에 실행 → `now.strftime("%Y-%m-%d")`와 수집 범위가 일치 → 문제 없음
- **디버깅/재집계**: `count_verifications`에 `target_date="2026-04-15"` 명시, `collect_messages(..., since="2d")`로 수집 범위 확장
- 이번 세션에선 어제치를 실제로 보내야 했으므로 one-off 스크립트를 작성해 처리:

```python
daily = count_verifications(msgs, cfg, target_date='2026-04-15')
send_daily_report(cfg['admin_chat_id'], daily, dry_run=False)
```

### 교훈

- "지금 이 순간 실행" 정책은 타임존/스케줄 경계 이슈를 만든다
- `--since`와 `target_date`가 느슨하게 결합돼 있어 디버거가 같은 명령을 자정 직후 돌리면 반드시 혼동
- launchd 스케줄(23:00) 이외 시간에 수동 실행할 땐 `target_date`를 명시하는 습관

---

## 이슈 5. 특정 멤버가 "2회"로 잡히는 오집계 (Post-review fix)

### 증상

PRD US-003 dry-run 완료 후, 한 멤버만 `counts`에 `2`로 표시됨. 실제 오늘은 1장만 업로드.

### 원인

- `kakaocli --since 1d`는 24시간 윈도우라 **전일 23:37** 사진까지 포함됨
- `counter.count_verifications`가 "고유 업로드 날짜 수"를 세는 구현이었음
- 전일 날짜 + 당일 날짜 = 2가지 날짜에 업로드 → `len(date_set) = 2` → 2회 집계

### 해결

`count_verifications`에 `target_date` 파라미터 추가 후, 메시지를 `sent_at.strftime("%Y-%m-%d") == target_date`로 선필터링:

```python
todays_msgs = [
    (sent_at, m) for m in filter_photo_messages(messages, photo_type)
    if (sent_at := parse_datetime(m.get("timestamp", "")))
       and sent_at.strftime("%Y-%m-%d") == today_str
]
```

### 검증

재실행: `raw_photo` 총량은 그대로, 해당 멤버만 `2 → 1` (정상)

---

## 이슈 6. Python 3.9 호환성 (PEP 604 런타임 평가)

### 증상

```python
def collect_messages(chat_id: int | None = None, ...):
    ...
```

실행 시 `TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'` (Python 3.9.6, 시스템 기본).

### 원인

- PEP 604 union 문법 (`int | None`)은 **런타임**에는 Python 3.10+ 이상에서만 동작
- Python 3.9도 `from __future__ import annotations`가 있으면 타입 어노테이션을 문자열로 지연 평가하여 호환 가능

### 해결

모든 모듈 최상단에 추가:

```python
from __future__ import annotations
```

영향 받은 파일: `collector.py`, `counter.py`, `main.py`, `storage.py`, `notifier.py`

### 교훈

- 시스템 python3이 3.9인 macOS에서 타입 어노테이션 쓸 땐 무조건 future import부터

---

## 이슈 7. NTMultiProfile 중복 멤버

### 증상

`kakaocli query "SELECT u.id, p.name FROM NTMember m JOIN NTUser u JOIN NTMultiProfile p"`로 멤버 목록을 뽑으면 한 유저당 9~13행이 나옴.

### 원인

- 카카오톡의 `NTMultiProfile` 테이블은 사용자가 채팅방마다 설정한 멀티프로필을 모두 저장
- 한 유저가 여러 오픈채팅방에서 각자 다른 닉네임을 쓰고 있으면 해당 유저에 대해 N개 행 반환
- `linkId` 컬럼이 프로필 연결 ID인데, `linkId = 0`이 "기본 프로필"(원본)

### 해결

```sql
LEFT JOIN NTMultiProfile p
  ON p.userId = u.id AND p.chatId = m.chatId AND p.linkId = 0
```

`linkId = 0` 필터를 JOIN 조건에 넣으면 유저당 정확히 1행.

### 교훈

- 카카오톡 DB 스키마는 문서화가 안 돼 있어서 `PRAGMA table_info`와 `SELECT DISTINCT`로 탐색 필수
- `NTMultiProfile.linkId` 같은 숨은 제약은 실제 데이터를 뽑아봐야 알 수 있음

---

## 채택하지 않은 대안들

### (A) Python pyobjc로 직접 AX API 제어

시도한 것: `AXUIElementCreateApplication`, `AXUIElementCopyAttributeValue`, `CGEventPost` 등으로 카카오톡 프로세스에 직접 접근해 채팅방 리스트 탐색 → 클릭 → 검색 필드 포커스 → 타이핑 → Enter.

- `/tmp/ax_probe[4~7].py` 등 수 개의 실험 스크립트
- `AXTable` 찾기 / `AXVisibleRows` 조회 / `AXSelectedRows` 설정 / `CGEventCreateMouseEvent` 클릭은 모두 **부분적으로** 동작
- 하지만 카카오톡의 커스텀 뷰 구조는 검색 필드 포커스, 리스트 항목 클릭 후 새 윈도우 개방 같은 흐름이 불안정하게 동작
- 재현성이 낮고 타이밍 의존성이 커서 실운영 불가
- **결론**: kmsg가 같은 일을 훨씬 안정적으로 처리하므로 직접 제어는 포기

### (B) MCP 기반 서버 (kmsg-mcp, inspirit941/kakao-bot-mcp-server)

- kmsg-mcp는 kmsg 바이너리를 래핑하는 MCP 서버
- 일반 CLI로는 `kmsg`를 직접 호출하는 쪽이 레이어가 적어 더 간결
- 에이전트 중심 워크플로라면 MCP가 유리하지만, 이 프로젝트는 launchd cron 실행이라 CLI 호출이 자연스러움
- **결론**: kmsg 바이너리 직접 호출

### (C) 카카오톡 웹/오픈API

- 카카오비즈 API는 기업용이고 개인 오픈채팅 송신은 지원하지 않음
- 카카오톡 웹(로그인/2FA 우회)은 정책 위반 가능
- **결론**: 로컬 Mac 앱 자동화가 유일한 현실적 경로

---

## 다음에 개선하면 좋을 것들

- `target_date`를 CLI 인자로 받아 언제든 수동 재집계 가능하도록 `main.py` 확장
- 주간 리포트 포맷에 달성률 퍼센트/막대그래프 추가
- kmsg 전송 실패 시 지수 백오프로 N회 재시도
- 알림 실패 시 fallback으로 이메일 또는 Slack webhook 병행
- launchd 대신 cron 지원 (`crontab -e`) 옵션 제공

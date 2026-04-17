# HT 운동 인증 카운터

## 프로젝트 개요
카카오톡 [#HT] 인증방의 운동 인증 사진 업로드를 자동 집계하는 시스템.
읽기는 kakaocli, 송신은 kmsg로 역할이 분리되어 있다.

## 아키텍처
- kakaocli (Swift CLI) → 카카오톡 Mac 로컬 DB 조회 **(읽기 전용)**
- kmsg (Swift CLI) → 카카오톡 Accessibility API 자동화 송신 **(쓰기 전용)**
- Python 3.12+ → 파싱/집계/저장
- SQLite → 집계 데이터 영구 저장
- launchd → 매일 **23:59 KST** 자동 실행 (launchd는 초 단위 미지원이라 23:59:00 트리거가 실질적 "하루의 마지막")

## 파일 구조
- main.py — 오케스트레이터
- collector.py — kakaocli로 메시지 수집
- counter.py — 사진 메시지 필터링, 인증 카운팅
- storage.py — SQLite CRUD, 멱등성 보장
- notifier.py — kmsg send로 운영진 채널 전달
- sync_members.py — kakaocli query로 방 멤버/이름 동기화
- config.json — 채팅방 이름, 멤버 목록, 설정값

## 핵심 규칙
- 모든 시간은 KST (Asia/Seoul) 기준
- 사진 인증 = NTChatMessage.type == 2 (검증 후 확정)
- 멤버당 하루 1회 캡 (daily_cap_per_member)
- 주 시작 = 월요일 00:00 KST
- msg_hash (SHA-256)로 멱등성 보장
- 수집 결과 0건이면 정상 처리하지 않고 에러 알림
- dry_run=true일 때 실제 전송하지 않음
- **kakaocli는 DB 조회/파싱 전용. 어떤 경로로도 송신에 쓰지 말 것.**
- **카톡으로 나가는 모든 메시지는 kmsg를 통한다.**

## 발송 주기
- 집계 대상: 실행 시점 KST 기준 "오늘" (00:00 ~ 23:59) 업로드된 사진만
- **매일 23:59 KST**: 그날의 데일리 리포트 운영진방 전송
- **일요일 23:59 KST**: 데일리 + 위클리 리포트 함께 전송 (`main.py`의 `is_sunday` 분기)
- launchd 재실행 기본 동작: Mac 잠자기 등으로 23:59 트리거를 놓쳤으면 깨어난 직후 한 번 자동 실행

## CLI 명령어

### 읽기 (kakaocli)
- `kakaocli messages --chat "HT" --since 1d --json` → 메시지 수집
- `kakaocli query "SQL"` → 로컬 DB 직접 쿼리 (멤버/방 정보 동기화 등)
- `kakaocli chats` → 채팅방 목록과 chat_id 확인

### 송신 (kmsg)
- `kmsg send "[#HT] 운영진방" "메시지"` → 운영진 채널로 전달
- `kmsg read "[#HT] 운영진방"` → 대상 방 열기 (AX 송신 전처리)
- `kmsg status` / `kmsg chats --json` → 상태·방 목록 확인

## 테스트
- 항상 `dry_run=true` 또는 `--dry-run` 플래그로 먼저 테스트
- config.json의 dry_run을 true로 유지하면서 개발
- 실제 송신을 확인해야 할 때는 admin_chat_id/admin_chat_name을 본인 전용 방으로 임시 바꾸고 kmsg로 테스트

# HT 운동 인증 카운터

## 프로젝트 개요
카카오톡 [#HT] 인증방의 운동 인증 사진 업로드를 kakaocli로 자동 집계하는 시스템.

## 아키텍처
- kakaocli (Swift CLI) → 카카오톡 Mac 로컬 DB 읽기 + UI 자동화 전송
- Python 3.12+ → 파싱/집계/저장
- SQLite → 집계 데이터 영구 저장
- launchd → 매일 23:00 자동 실행

## 파일 구조
- main.py — 오케스트레이터
- collector.py — kakaocli 호출, 메시지 수집
- counter.py — 사진 메시지 필터링, 인증 카운팅
- storage.py — SQLite CRUD, 멱등성 보장
- notifier.py — kakaocli send로 운영진 채널 전달
- config.json — 채팅방 이름, 멤버 목록, 설정값

## 핵심 규칙
- 모든 시간은 KST (Asia/Seoul) 기준
- 사진 인증 = NTChatMessage.type == 2 (검증 후 확정)
- 멤버당 하루 1회 캡 (daily_cap_per_member)
- 주 시작 = 월요일 00:00 KST
- msg_hash (SHA-256)로 멱등성 보장
- 수집 결과 0건이면 정상 처리하지 않고 에러 알림
- dry_run=true일 때 실제 전송하지 않음

## kakaocli 명령어
- kakaocli messages --chat "HT" --since 1d --json → 메시지 수집
- kakaocli query "SQL" → 직접 DB 쿼리
- kakaocli send "운영진방" "메시지" → 결과 전달
- kakaocli send --me _ "테스트" → 나에게 테스트 전송

## 테스트
- 항상 --me 또는 --dry-run으로 먼저 테스트
- config.json의 dry_run을 true로 유지하면서 개발

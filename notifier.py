"""kmsg send를 통한 카카오톡 운영진 채널 전달.

메시지 템플릿은 `templates/*.md` 파일에서 관리한다.
반복되는 멤버 리스트 / 다회 업로드 / 주간 상세 섹션은 코드에서 미리
문자열 블록으로 렌더해 템플릿의 {..._block} 플레이스홀더에 치환.

플레이스홀더:
  templates/daily.md    {date}, {capped_count}, {multi_upload_block}, {member_list_block}
  templates/weekly.md   {week_key}, {member_details_block}
  templates/error.md    {error_msg}
"""
from __future__ import annotations

import subprocess
import logging
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

DEFAULT_TEMPLATES_DIR = "templates"

_KST = ZoneInfo("Asia/Seoul")
_WEEKDAYS_KO = ["월", "화", "수", "목", "금", "토", "일"]

RAISE_MAIN_WINDOW_SCRIPT = (
	'tell application "System Events" to tell process "KakaoTalk" '
	'to perform action "AXRaise" of (first window whose name is "카카오톡")'
)

# 카톡 사이드바에서 '채팅' 탭으로 전환 (Cmd+2).
# kmsg는 현재 활성 사이드바 탭 안에서만 채팅방을 검색하므로,
# 친구/오픈채팅 탭이 활성이면 채팅방을 못 찾고 rc=1로 실패.
#
# 주의: System Events의 keystroke는 실제로 OS 전역 입력 스트림에 키를 쏘기
# 때문에 `tell process "KakaoTalk"` 문법에도 불구하고 진짜 frontmost 앱이
# 키를 받는다. 그래서 카톡이 frontmost로 올라와 있을 때만 이 스크립트를
# 실행해야 한다. 아니면 엉뚱한 앱(Claude Code / Electron 등)이 Cmd+2를
# 받아 시스템 Funk 경고음이 반복 발생한다 (macOS 기본 bonk).
SWITCH_TO_CHAT_TAB_SCRIPT = (
	'tell application "System Events" to tell process "KakaoTalk" '
	'to keystroke "2" using command down'
)

FRONTMOST_CHECK_SCRIPT = (
	'tell application "System Events" to get name of first process '
	'whose frontmost is true'
)


def _load_template(config: dict, name: str) -> str:
	base = Path(config.get("templates_dir", DEFAULT_TEMPLATES_DIR))
	path = base / f"{name}.md"
	if not path.is_absolute():
		project_root = Path(__file__).resolve().parent
		path = project_root / path
	return path.read_text(encoding="utf-8").rstrip("\n")


def _format_ts(iso_str: str) -> str:
	"""ISO 8601 (UTC or naive) → 'MM-DD 요일 HH:MM' (KST)"""
	if not iso_str:
		return "?"
	try:
		dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
		if dt.tzinfo is None:
			dt = dt.replace(tzinfo=_KST)
		dt = dt.astimezone(_KST)
		return dt.strftime(f"%m-%d {_WEEKDAYS_KO[dt.weekday()]} %H:%M")
	except Exception:
		return iso_str


def _render_member_list_block(counts: dict) -> str:
	"""daily: '인증자: 홍길동, 김철수, 박영희' (없으면 빈 문자열)"""
	if not counts:
		return ""
	names = ", ".join(sorted(counts.keys()))
	return f"\n\n인증자: {names}"


def _render_multi_upload_block(raw_counts: dict) -> str:
	"""daily: 2회 이상 올린 사람만 별도 섹션 (없으면 빈 문자열)"""
	multi = {m: c for m, c in raw_counts.items() if c >= 2}
	if not multi:
		return ""
	rows = [f"  {m}: {c}회" for m, c in sorted(multi.items(), key=lambda kv: (-kv[1], kv[0]))]
	return "\n\n[다회 업로드]\n" + "\n".join(rows)


def _render_member_details_block(members: list) -> str:
	"""weekly: 모든 멤버 × (횟수 + 날짜 시:분 리스트). 횟수 내림차순."""
	if not members:
		return ""
	parts = []
	for row in members:
		header = f"{row['name']}: {row['count']}회"
		if row["timestamps"]:
			lines = [f"  - {_format_ts(ts)}" for ts in row["timestamps"]]
			parts.append(header + "\n" + "\n".join(lines))
		else:
			parts.append(header)
	return "\n\n" + "\n\n".join(parts)


def send_daily_report(admin_chat: str, summary: dict, config: dict, dry_run: bool = True):
	"""일별 집계 결과를 운영진 채널에 전송"""
	template = _load_template(config, "daily")
	message = template.format(
		date=summary.get("date", "unknown"),
		capped_count=summary.get("capped_count", 0),
		multi_upload_block=_render_multi_upload_block(summary.get("raw_counts", {})),
		member_list_block=_render_member_list_block(summary.get("counts", {})),
	)
	_send(admin_chat, message, dry_run)


def send_weekly_report(admin_chat: str, summary: dict, config: dict, dry_run: bool = True):
	"""주간 집계 결과를 운영진 채널에 전송"""
	template = _load_template(config, "weekly")
	message = template.format(
		week_key=summary.get("week_key", "unknown"),
		week_range=summary.get("week_range", summary.get("week_key", "unknown")),
		member_details_block=_render_member_details_block(summary.get("members", [])),
	)
	_send(admin_chat, message, dry_run)


def send_error_alert(admin_chat: str, error_msg: str, config: dict, dry_run: bool = True):
	"""집계 실패 시 운영진에 알림"""
	template = _load_template(config, "error")
	message = template.format(error_msg=error_msg)
	_send(admin_chat, message, dry_run)


def _is_kakaotalk_frontmost() -> bool:
	"""현재 frontmost 앱이 카카오톡인지 확인."""
	try:
		r = subprocess.run(
			["osascript", "-e", FRONTMOST_CHECK_SCRIPT],
			capture_output=True, text=True, timeout=3,
		)
		return "KakaoTalk" in (r.stdout or "")
	except Exception:
		return False


def _prepare_kakaotalk():
	"""kmsg 호출 전 카톡을 전송 가능 상태로 정렬.

	카톡이 실제 frontmost로 올라올 때까지 대기한 다음에만 AXRaise와
	Cmd+2 keystroke를 실행한다. 프론트가 아닌 상태에서 keystroke를
	쏘면 엉뚱한 앱(예: Claude Code/Electron)이 키를 받아 macOS Funk
	경고음이 연속 발생한다 (수동 테스트 중 '굉음' 원인).

	카톡을 프론트로 올리지 못하면 keystroke를 생략하고, kmsg의
	--deep-recovery 옵션이 알아서 윈도우 상태를 복구하도록 맡긴다.
	launchd 백그라운드 실행 경로에서는 어차피 다른 앱이 frontmost가
	아니므로 영향 없음.
	"""
	try:
		subprocess.run(
			["osascript", "-e", 'tell application "KakaoTalk" to activate'],
			capture_output=True, text=True, timeout=5,
		)

		# 카톡이 실제 frontmost로 올라올 때까지 최대 3초 대기 (15 × 0.2s).
		# 사용자가 Claude Code 등 다른 앱에 포커스를 유지하고 있으면
		# activate만으로는 포커스를 훔쳐오지 못한다. 이 경우 keystroke
		# 실행을 포기해야 엉뚱한 앱에 키가 안 들어간다.
		kt_ready = False
		for _ in range(15):
			if _is_kakaotalk_frontmost():
				kt_ready = True
				break
			time.sleep(0.2)

		if not kt_ready:
			logger.warning(
				"카카오톡이 frontmost가 아님 — keystroke 생략, "
				"kmsg --deep-recovery로 복구 시도"
			)
			return

		subprocess.run(
			["osascript", "-e", RAISE_MAIN_WINDOW_SCRIPT],
			capture_output=True, text=True, timeout=5,
		)
		subprocess.run(
			["osascript", "-e", SWITCH_TO_CHAT_TAB_SCRIPT],
			capture_output=True, text=True, timeout=5,
		)
		time.sleep(0.4)
	except Exception as e:
		logger.warning(f"카카오톡 준비 단계 실패(무시하고 진행): {e}")


def _send(chat_target: str, message: str, dry_run: bool):
	"""kmsg send 실행. chat_target은 kmsg chat_id(chat_XXXX) 또는 채팅방 제목."""
	if dry_run:
		logger.info(f"[DRY RUN] → {chat_target}:\n{message}")
		return

	_prepare_kakaotalk()

	# --keep-window: 긴 메시지 전송 후 파일 업/다운로드 파이프라인과 창 닫기
	# 타이밍이 겹쳐 "채팅방을 닫으시겠습니까?" 모달이 뜨는 것을 방지.
	# --deep-recovery: 프론트가 카톡이 아니거나 사이드바 탭이 리셋된 상태
	# 등에서도 kmsg가 자체적으로 윈도우 복구를 시도하도록 함 (우리가
	# osascript keystroke 생략했을 때 폴백).
	base = ["kmsg", "send", "--keep-window", "--deep-recovery"]
	if chat_target.startswith("chat_"):
		cmd = base + ["--chat-id", chat_target, message]
	else:
		cmd = base + [chat_target, message]

	try:
		result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
		if result.returncode != 0:
			logger.error(f"kmsg send 실패 (rc={result.returncode}): {result.stderr.strip()}")
		else:
			logger.info(f"전송 완료 → {chat_target}")
			# 파일 업로드/다운로드 완료를 기다리기 위한 안전 delay.
			time.sleep(1.5)
	except subprocess.TimeoutExpired:
		logger.error("kmsg send 타임아웃")
	except Exception as e:
		logger.error(f"전송 예외: {e}")

"""카카오톡 운영진 채널 전달.

메시지 템플릿은 `templates/*.md` 파일에서 관리한다.
반복되는 멤버 리스트 / 다회 업로드 / 주간 상세 섹션은 코드에서 미리
문자열 블록으로 렌더해 템플릿의 {..._block} 플레이스홀더에 치환.

기본 송신 방식(`admin_sender = "kmsg"`)은:
1. 보낼 메시지를 모두 백그라운드에서 준비
2. `scripts/ensure_kakao_chat.py`로 대상 채팅창과 입력창만 확보
3. 마지막에만 입력창 `AXValue`에 본문을 직접 넣고 Enter

즉, 긴 본문을 한 글자씩 타이핑하지 않고, 클립보드 붙여넣기에도
의존하지 않는다. 포커스가 흔들려도 엉뚱한 앱에 장문이 새는 위험을
줄이는 것이 목적이다.

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
DEFAULT_ADMIN_SENDER = "kmsg"
PROJECT_ROOT = Path(__file__).resolve().parent
SCRIPT_DIR = PROJECT_ROOT / "scripts"
ENSURE_CHAT_SCRIPT = SCRIPT_DIR / "ensure_kakao_chat.py"

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

FOCUS_CHAT_WINDOW_SCRIPT = """
on run argv
	set targetTitle to item 1 of argv
	tell application "KakaoTalk" to activate
	tell application "System Events"
		tell process "KakaoTalk"
			set targetWindow to first window whose name is targetTitle
			perform action "AXRaise" of targetWindow
		end tell
	end tell
end run
""".strip()

SET_TEXT_AND_SEND_SCRIPT = """
on run argv
	set targetTitle to item 1 of argv
	set messageBody to item 2 of argv
	tell application "System Events"
		set frontName to name of first process whose frontmost is true
		if frontName is not "KakaoTalk" then error "KakaoTalk is not frontmost"
		tell process "KakaoTalk"
			if name of front window is not targetTitle then error "Front window is not target chat"
			if not (exists text area 1 of scroll area 2 of front window) then error "Target chat input not found"
			set value of text area 1 of scroll area 2 of front window to messageBody
			delay 0.05
			key code 36
		end tell
	end tell
end run
""".strip()

WINDOW_EXISTS_SCRIPT = """
on run argv
	set targetTitle to item 1 of argv
	tell application "System Events"
		tell process "KakaoTalk"
			if exists first window whose name is targetTitle then
				return "true"
			end if
		end tell
	end tell
	return "false"
end run
""".strip()


def _load_template(config: dict, name: str) -> str:
	base = Path(config.get("templates_dir", DEFAULT_TEMPLATES_DIR))
	path = base / f"{name}.md"
	if not path.is_absolute():
		path = PROJECT_ROOT / path
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
	_send(admin_chat, message, dry_run, config)


def send_weekly_report(admin_chat: str, summary: dict, config: dict, dry_run: bool = True):
	"""주간 집계 결과를 운영진 채널에 전송"""
	template = _load_template(config, "weekly")
	message = template.format(
		week_key=summary.get("week_key", "unknown"),
		week_range=summary.get("week_range", summary.get("week_key", "unknown")),
		member_details_block=_render_member_details_block(summary.get("members", [])),
	)
	_send(admin_chat, message, dry_run, config)


def send_error_alert(admin_chat: str, error_msg: str, config: dict, dry_run: bool = True):
	"""집계 실패 시 운영진에 알림"""
	template = _load_template(config, "error")
	message = template.format(error_msg=error_msg)
	_send(admin_chat, message, dry_run, config)


def _sender_backend(config: dict) -> str:
	"""송신 백엔드 결정. 기본은 안전한 AXValue 방식."""
	return str(config.get("admin_sender", DEFAULT_ADMIN_SENDER)).strip() or DEFAULT_ADMIN_SENDER


def _build_direct_send_cmd(chat_target: str) -> list[str]:
	"""기존 kmsg 직접 타이핑 전송 명령."""
	base = ["kmsg", "send", "--keep-window", "--deep-recovery"]
	if chat_target.startswith("chat_"):
		return base + ["--chat-id", chat_target]
	return base + [chat_target]


def _resolve_open_chat_name(chat_target: str, config: dict) -> str:
	"""AX 전송용으로 실제 '열 채팅방 이름' 결정."""
	if not chat_target.startswith("chat_"):
		return chat_target
	return str(config.get("admin_chat_name", "")).strip()


def _build_open_chat_cmd(chat_target: str, config: dict) -> list[str]:
	"""kmsg read를 이용해 대상 채팅방만 열기."""
	open_name = _resolve_open_chat_name(chat_target, config)
	if not open_name:
		raise ValueError(
			"AX 전송은 admin_chat_name이 필요합니다. "
			"config.json에 admin_chat_name을 설정하세요."
		)
	return [
		"kmsg", "read", open_name,
		"--limit", "1",
		"--keep-window",
		"--deep-recovery",
	]


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


def _run_osascript(script: str, *args: str, timeout: int = 5) -> subprocess.CompletedProcess:
	"""AppleScript 실행 헬퍼."""
	cmd = ["osascript", "-e", script]
	if args:
		cmd.extend(["--", *args])
	return subprocess.run(
		cmd,
		capture_output=True,
		text=True,
		timeout=timeout,
	)


def _open_chat_window(chat_target: str, config: dict):
	"""대상 채팅방만 열고 유지한다. 아직 포커스는 강제하지 않는다."""
	result = subprocess.run(
		_build_open_chat_cmd(chat_target, config),
		capture_output=True,
		text=True,
		timeout=60,
	)
	if result.returncode != 0:
		raise RuntimeError(f"kmsg read 실패 (rc={result.returncode}): {result.stderr.strip()}")


def _ensure_chat_ready(chat_target: str, config: dict):
	"""레포 내 상태 정리 스크립트로 올바른 채팅창을 확보."""
	open_name = _resolve_open_chat_name(chat_target, config)
	if not open_name:
		raise RuntimeError("운영진 채팅방 이름이 비어 있어 상태 정리 스크립트를 실행할 수 없습니다.")
	if not ENSURE_CHAT_SCRIPT.exists():
		raise RuntimeError(f"채팅 상태 정리 스크립트가 없습니다: {ENSURE_CHAT_SCRIPT}")

	result = subprocess.run(
		["python3", str(ENSURE_CHAT_SCRIPT), "--chat", open_name],
		capture_output=True,
		text=True,
		timeout=90,
	)
	if result.returncode != 0:
		raise RuntimeError(
			f"채팅 상태 정리 실패 (rc={result.returncode}): "
			f"{result.stderr.strip() or result.stdout.strip()}"
		)


def _chat_window_exists(chat_target: str, config: dict) -> bool:
	"""대상 채팅창이 이미 떠 있으면 재검색하지 않는다."""
	open_name = _resolve_open_chat_name(chat_target, config)
	if not open_name:
		return False
	try:
		result = _run_osascript(WINDOW_EXISTS_SCRIPT, open_name, timeout=5)
		return result.returncode == 0 and result.stdout.strip() == "true"
	except Exception:
		return False


def _focus_chat_window(chat_target: str, config: dict):
	"""마지막 순간에만 대상 채팅창을 정확히 앞으로 올린다."""
	open_name = _resolve_open_chat_name(chat_target, config)
	if not open_name:
		raise RuntimeError("AX 전송용 대상 채팅창 이름이 비어 있습니다.")

	result = _run_osascript(FOCUS_CHAT_WINDOW_SCRIPT, open_name, timeout=5)
	if result.returncode != 0:
		raise RuntimeError(
			f"대상 채팅창 포커싱 실패: {result.stderr.strip() or result.stdout.strip()}"
		)

	for _ in range(10):
		if _is_kakaotalk_frontmost():
			return
		time.sleep(0.1)

	raise RuntimeError("대상 채팅창을 frontmost로 올리지 못해 AX 전송을 중단합니다.")


def _set_text_and_send(chat_target: str, message: str, config: dict):
	"""대상 채팅창 입력창에 본문을 직접 넣고 Enter."""
	open_name = _resolve_open_chat_name(chat_target, config)
	_focus_chat_window(chat_target, config)

	result = _run_osascript(SET_TEXT_AND_SEND_SCRIPT, open_name, message, timeout=5)
	if result.returncode != 0:
		raise RuntimeError(f"본문 세팅 전송 실패: {result.stderr.strip() or result.stdout.strip()}")


def _send_via_kmsg_ax(chat_target: str, message: str, config: dict):
	"""기본 안전 경로: 채팅창 준비 후 본문 직접 세팅 전송."""
	if not _chat_window_exists(chat_target, config):
		_ensure_chat_ready(chat_target, config)
	_set_text_and_send(chat_target, message, config)
	logger.info(f"본문 직접 세팅 전송 완료 → {chat_target}")
	time.sleep(0.8)


def _send_via_kmsg_direct(chat_target: str, message: str):
	"""레거시 경로: kmsg가 본문을 직접 타이핑."""
	_prepare_kakaotalk()
	cmd = _build_direct_send_cmd(chat_target) + [message]
	result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
	if result.returncode != 0:
		raise RuntimeError(f"kmsg send 실패 (rc={result.returncode}): {result.stderr.strip()}")
	logger.info(f"직접 타이핑 전송 완료 → {chat_target}")
	time.sleep(1.5)


def _send(chat_target: str, message: str, dry_run: bool, config: dict):
	"""운영진 채널 전송. 기본은 kmsg-assisted AXValue send."""
	if dry_run:
		logger.info(f"[DRY RUN] → {chat_target}:\n{message}")
		return

	try:
		sender = _sender_backend(config)
		if sender == "kmsg_direct":
			_send_via_kmsg_direct(chat_target, message)
		else:
			_send_via_kmsg_ax(chat_target, message, config)
	except subprocess.TimeoutExpired:
		logger.error("전송 타임아웃")
	except Exception as e:
		logger.error(f"전송 예외: {e}")


def send_messages_batch(admin_chat: str, messages: list[str], config: dict, dry_run: bool = True):
	"""여러 메시지를 모두 준비한 뒤 한 채팅창에 연속 전송."""
	if dry_run:
		for idx, message in enumerate(messages, start=1):
			logger.info(f"[DRY RUN {idx}/{len(messages)}] → {admin_chat}:\n{message}")
		return

	sender = _sender_backend(config)
	if sender == "kmsg_direct":
		for message in messages:
			_send_via_kmsg_direct(admin_chat, message)
		return

	if not messages:
		return

	# 호출자가 메시지 리스트를 모두 만든 뒤 들어온 상태에서, 여기서는
	# 채팅창 준비와 실제 전송만 처리한다.
	_ensure_chat_ready(admin_chat, config)
	for message in messages:
		_set_text_and_send(admin_chat, message, config)
		logger.info(f"배치 본문 직접 세팅 전송 완료 → {admin_chat}")
		time.sleep(0.8)

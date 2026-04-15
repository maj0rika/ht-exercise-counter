"""kmsg send를 통한 카카오톡 운영진 채널 전달"""
import subprocess
import logging

logger = logging.getLogger(__name__)

AI_SIGNATURE = "\n\n— AI 자동 송신 (HT 운동 인증 카운터)"

RAISE_MAIN_WINDOW_SCRIPT = (
	'tell application "System Events" to tell process "KakaoTalk" '
	'to perform action "AXRaise" of (first window whose name is "카카오톡")'
)


def send_daily_report(admin_chat: str, summary: dict, dry_run: bool = True):
	"""일별 집계 결과를 운영진 채널에 전송"""
	date = summary.get("date", "unknown")
	counts = summary.get("counts", {})
	flagged = summary.get("flagged", [])

	lines = [f"[HT 인증 집계] {date}"]
	lines.append(f"총 인증: {summary.get('capped_count', 0)}건")
	lines.append("")

	for member, count in sorted(counts.items()):
		lines.append(f"  {member}: {count}회")

	if flagged:
		lines.append("")
		lines.append("[검토 필요]")
		for f in flagged:
			lines.append(f"  {f['author']}: {f['reason']}")

	message = "\n".join(lines) + AI_SIGNATURE
	_send(admin_chat, message, dry_run)


def send_weekly_report(admin_chat: str, summary: dict, dry_run: bool = True):
	"""주간 집계 결과를 운영진 채널에 전송"""
	week = summary.get("week_key", "unknown")

	lines = [f"[HT 주간 인증 요약] {week}"]
	lines.append("")

	achieved = summary.get("achieved", [])
	not_achieved = summary.get("not_achieved", [])

	if achieved:
		lines.append(f"달성 ({len(achieved)}명):")
		for name in achieved:
			count = summary.get("member_counts", {}).get(name, 0)
			lines.append(f"  {name}: {count}회 ✅")

	if not_achieved:
		lines.append("")
		lines.append(f"미달성 ({len(not_achieved)}명):")
		for item in not_achieved:
			lines.append(f"  {item['name']}: {item['count']}회 (부족: {item['shortfall']}회) ❌")

	message = "\n".join(lines) + AI_SIGNATURE
	_send(admin_chat, message, dry_run)


def send_error_alert(admin_chat: str, error_msg: str, dry_run: bool = True):
	"""집계 실패 시 운영진에 알림"""
	message = f"[HT 인증 시스템 오류]\n{error_msg}\n수동 확인이 필요합니다." + AI_SIGNATURE
	_send(admin_chat, message, dry_run)


def _raise_main_window():
	"""kmsg가 올바른 '카카오톡' 메인 윈도우를 검사하도록 강제 raise."""
	try:
		subprocess.run(
			["osascript", "-e", RAISE_MAIN_WINDOW_SCRIPT],
			capture_output=True,
			text=True,
			timeout=5,
		)
	except Exception as e:
		logger.warning(f"카카오톡 메인 윈도우 raise 실패(무시하고 진행): {e}")


def _send(chat_target: str, message: str, dry_run: bool):
	"""kmsg send 실행. chat_target은 kmsg chat_id(chat_XXXX) 또는 채팅방 제목."""
	if dry_run:
		logger.info(f"[DRY RUN] → {chat_target}:\n{message}")
		return

	_raise_main_window()

	if chat_target.startswith("chat_"):
		cmd = ["kmsg", "send", "--chat-id", chat_target, message]
	else:
		cmd = ["kmsg", "send", chat_target, message]

	try:
		result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
		if result.returncode != 0:
			logger.error(f"kmsg send 실패 (rc={result.returncode}): {result.stderr.strip()}")
		else:
			logger.info(f"전송 완료 → {chat_target}")
	except subprocess.TimeoutExpired:
		logger.error("kmsg send 타임아웃")
	except Exception as e:
		logger.error(f"전송 예외: {e}")

"""kakaocli를 호출하여 인증방 메시지를 JSON으로 수집"""
from __future__ import annotations

import subprocess
import json
import time


class CollectionError(Exception):
	pass


_DB_LOCK_TOKENS = (
	"file is not a database",
	"database is locked",
	"disk I/O error",
)


def _run_kakaocli(cmd: list[str], label: str, timeout: int = 60, max_retries: int = 6) -> subprocess.CompletedProcess:
	"""kakaocli 호출 + DB 락 자동 재시도.

	카카오톡이 로컬 DB에 write 중이면 'file is not a database' 같은 에러가
	일시적으로 나올 수 있다. 지수 백오프로 재시도한다.
	"""
	last_err = ""
	for attempt in range(1, max_retries + 1):
		try:
			result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
		except subprocess.TimeoutExpired:
			raise CollectionError(f"kakaocli 명령 타임아웃 ({timeout}초): {label}")
		except FileNotFoundError:
			raise CollectionError("kakaocli가 PATH에 없음. brew install 확인 필요")

		if result.returncode == 0:
			if attempt > 1:
				print(f"  [{label}] DB 락 해소 완료 (재시도 {attempt-1}회)", flush=True)
			return result

		last_err = (result.stderr or "").strip()
		if any(tok in last_err for tok in _DB_LOCK_TOKENS):
			print(f"  [{label}] DB 잠김 — 대기 {attempt}/{max_retries}...", flush=True)
			time.sleep(0.5 * attempt)
			continue
		break

	raise CollectionError(f"kakaocli 실패 ({label}): {last_err}")


def collect_messages(
	chat_id: int | None = None,
	chat_name: str | None = None,
	since: str = "1d",
	limit: int = 500,
) -> list[dict]:
	"""
	kakaocli messages 호출 → JSON 파싱 → 메시지 리스트 반환.

	우선순위: chat_id > chat_name. 둘 다 없으면 에러.

	Returns (kakaocli 0.4.x 스키마):
		[{"chat_id", "id", "is_from_me", "sender_id", "text", "timestamp", "type"}, ...]
	"""
	if chat_id is None and not chat_name:
		raise CollectionError("chat_id 또는 chat_name 중 하나는 반드시 지정해야 함")

	cmd = ["kakaocli", "messages", "--since", since, "--limit", str(limit), "--json"]
	if chat_id is not None:
		cmd.extend(["--chat-id", str(chat_id)])
	else:
		cmd.extend(["--chat", chat_name])

	result = _run_kakaocli(cmd, label=f"messages --since {since} --limit {limit}", timeout=60)

	if not result.stdout.strip():
		raise CollectionError("kakaocli 출력이 비어있음. DB 동기화 상태 확인 필요")

	try:
		messages = json.loads(result.stdout)
	except json.JSONDecodeError as e:
		raise CollectionError(f"JSON 파싱 실패: {e}")

	if not isinstance(messages, list):
		messages = [messages] if messages else []

	return messages


def collect_via_raw_query(chat_id: int, date_str: str, photo_type_int: int = 2) -> list[dict]:
	"""
	kakaocli query로 특정 날짜의 사진 메시지를 직접 SQL 조회.
	CLI의 messages 명령이 정규화한 JSON과 달리, NTChatMessage 원시 컬럼을 사용.
	"""
	sql = (
		"SELECT logId, authorId, type, message, sentAt "
		"FROM NTChatMessage "
		f"WHERE chatId = {int(chat_id)} "
		f"  AND type = {int(photo_type_int)} "
		f"  AND date(sentAt, 'unixepoch', 'localtime') = '{date_str}' "
		"ORDER BY sentAt ASC"
	)

	cmd = ["kakaocli", "query", sql]
	result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

	if result.returncode != 0:
		raise CollectionError(f"SQL 쿼리 실패: {result.stderr.strip()}")

	return json.loads(result.stdout) if result.stdout.strip() else []

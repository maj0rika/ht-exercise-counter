"""수집된 메시지에서 인증 횟수를 집계 (kakaocli 0.4.x JSON 스키마 기준)"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from collections import defaultdict
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def week_range_str(week_key: str) -> str:
	"""'2026-W15' → '2026.04.06 ~ 2026.04.12' (ISO 월~일)"""
	try:
		monday = datetime.strptime(f"{week_key}-1", "%G-W%V-%u")
		sunday = monday + timedelta(days=6)
		return f"{monday.strftime('%Y.%m.%d')} ~ {sunday.strftime('%Y.%m.%d')}"
	except ValueError:
		return week_key


def count_verifications(messages: list[dict], config: dict, target_date: str | None = None) -> dict:
	"""
	메시지 리스트 → 멤버별 일별 인증 집계

	target_date(YYYY-MM-DD)가 주어지면 해당 날짜 메시지만 집계.
	기본값은 KST 기준 오늘.
	"""
	photo_type = config.get("photo_message_type", "photo")
	dup_window = config.get("duplicate_window_minutes", 3)
	user_map = build_user_map(config.get("members", []))
	today_str = target_date or datetime.now(KST).strftime("%Y-%m-%d")

	todays_msgs = []
	for m in filter_photo_messages(messages, photo_type):
		sent_at = parse_datetime(m.get("timestamp", ""))
		if sent_at and sent_at.strftime("%Y-%m-%d") == today_str:
			todays_msgs.append((sent_at, m))
	todays_msgs.sort(key=lambda x: x[0])

	daily_counts: dict[str, set[str]] = defaultdict(set)
	raw_counts: dict[str, int] = defaultdict(int)
	flagged: list[dict] = []
	last_upload: dict[str, datetime] = {}

	for sent_at, msg in todays_msgs:
		sender_id = msg.get("sender_id")
		canonical = user_map.get(sender_id, f"unknown_{sender_id}")

		if canonical in last_upload:
			delta_min = (sent_at - last_upload[canonical]).total_seconds() / 60
			if 0 <= delta_min < dup_window:
				flagged.append({
					"author": canonical,
					"reason": f"{dup_window}분 내 연속 업로드 ({delta_min:.0f}분 간격)",
					"timestamp": msg.get("timestamp"),
					"id": msg.get("id"),
				})

		last_upload[canonical] = sent_at
		daily_counts[canonical].add(today_str)
		raw_counts[canonical] += 1

	counts = {member: len(dates) for member, dates in daily_counts.items()}

	return {
		"date": today_str,
		"counts": counts,
		"raw_counts": dict(raw_counts),
		"flagged": flagged,
		"raw_photo_count": len(todays_msgs),
		"capped_count": sum(counts.values()),
	}


def weekly_summary(
	member_timestamps: dict,
	config: dict,
	week_key: str | None = None,
	active_members: list | None = None,
) -> dict:
	"""멤버별 timestamp 리스트 → 주간 요약.

	Args:
		member_timestamps: {canonical_name: [iso_str, ...]}
		active_members: 현재 채팅방에 있는 canonical 이름 리스트 (옵션).
			None이면 config.members 전체 사용. 지정하면 이 리스트 안에 있는
			멤버만 리포트에 포함 → "방을 나간 멤버 제외" 효과.

	Returns:
		{
			"week_key": "YYYY-WNN",
			"members": [{"name", "count", "timestamps"}, ...]  # count DESC, name ASC
		}
	"""
	if active_members is not None:
		all_members = list(active_members)
	else:
		all_members = [m["canonical"] for m in config.get("members", [])]
	active_set = set(all_members)

	rows = []
	for name in all_members:
		stamps = sorted(member_timestamps.get(name, []))
		rows.append({"name": name, "count": len(stamps), "timestamps": stamps})

	# 활성 리스트에 없는 이름(ex. unknown_xxx)은 제외한다.
	# active_members를 지정했을 때는 방을 나간 사람 / 낯선 발신자 모두 버림.

	rows.sort(key=lambda r: (-r["count"], r["name"]))

	if week_key is None:
		week_key = datetime.now(KST).strftime("%G-W%V")
	return {
		"week_key": week_key,
		"week_range": week_range_str(week_key),
		"members": rows,
	}


def filter_photo_messages(messages: list[dict], photo_type) -> list[dict]:
	"""
	사진 메시지 필터링.
	kakaocli JSON은 type 필드를 "photo"/"text" 등 string 라벨로 반환.
	"""
	accepted = {photo_type} if not isinstance(photo_type, (list, tuple, set)) else set(photo_type)
	return [m for m in messages if m.get("type") in accepted]


def build_user_map(members: list[dict]) -> dict[int, str]:
	"""user_id(int) → canonical 매핑"""
	return {m["user_id"]: m["canonical"] for m in members if "user_id" in m}


def parse_datetime(dt_str: str) -> datetime | None:
	"""ISO 8601 → KST datetime"""
	if not dt_str:
		return None
	try:
		dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
		if dt.tzinfo is None:
			dt = dt.replace(tzinfo=KST)
		return dt.astimezone(KST)
	except (ValueError, TypeError):
		return None


def generate_msg_hash(sender_id, timestamp: str, msg_type: str) -> str:
	"""중복 방지용 메시지 해시"""
	raw = f"{sender_id}|{timestamp}|{msg_type}"
	return hashlib.sha256(raw.encode()).hexdigest()

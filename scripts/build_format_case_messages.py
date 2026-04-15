#!/usr/bin/env python3
"""포맷 검증용 카카오톡 메시지 6건(안내 2 + 케이스 4) 생성.

출력 형식:
{
  "messages": ["...", "..."]
}
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import OrderedDict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from counter import weekly_summary
from notifier import (
	_load_template,
	_render_member_details_block,
	_render_member_list_block,
	_render_multi_upload_block,
)
from storage import Storage


def load_json(path: Path) -> dict:
	with path.open(encoding="utf-8") as f:
		return json.load(f)


def build_daily_summary(db_path: str, date_key: str) -> dict:
	conn = sqlite3.connect(db_path)
	conn.row_factory = sqlite3.Row
	rows = conn.execute(
		"""
		SELECT member_name, COUNT(*) AS raw_count
		FROM verifications
		WHERE date_key = ?
		GROUP BY member_name
		ORDER BY member_name
		""",
		(date_key,),
	).fetchall()
	conn.close()

	raw_counts = {row["member_name"]: int(row["raw_count"]) for row in rows}
	counts = {name: 1 for name in raw_counts}
	return {
		"date": date_key,
		"counts": counts,
		"raw_counts": raw_counts,
		"flagged": [],
		"raw_photo_count": sum(raw_counts.values()),
		"capped_count": len(counts),
	}


def render_daily_message(config: dict, summary: dict) -> str:
	template = _load_template(config, "daily")
	return template.format(
		date=summary.get("date", "unknown"),
		capped_count=summary.get("capped_count", 0),
		multi_upload_block=_render_multi_upload_block(summary.get("raw_counts", {})),
		member_list_block=_render_member_list_block(summary.get("counts", {})),
	)


def render_weekly_message(config: dict, summary: dict) -> str:
	template = _load_template(config, "weekly")
	return template.format(
		week_key=summary.get("week_key", "unknown"),
		week_range=summary.get("week_range", summary.get("week_key", "unknown")),
		member_details_block=_render_member_details_block(summary.get("members", [])),
	)


def build_weekly_summary(config: dict, db_path: str, week_key: str) -> dict:
	db = Storage(db_path)
	member_timestamps = db.get_week_member_timestamps(week_key)
	db.close()
	active_members = [
		name for name in OrderedDict.fromkeys(
			row["member_name"] for row in sorted(
				({"member_name": k, "count": len(v)} for k, v in member_timestamps.items()),
				key=lambda item: (-item["count"], item["member_name"]),
			)
		)
		if member_timestamps.get(name)
	]
	return weekly_summary(
		member_timestamps,
		config,
		week_key=week_key,
		active_members=active_members,
	)


def build_messages(config: dict, daily_date: str, current_week: str, previous_week: str) -> list[str]:
	db_path = config["db_path"]
	if not Path(db_path).is_absolute():
		db_path = str(PROJECT_ROOT / db_path)

	daily_normal = build_daily_summary(db_path, daily_date)
	daily_multi = {
		**daily_normal,
		"raw_counts": dict(daily_normal["raw_counts"]),
	}
	multi_targets = sorted(daily_multi["raw_counts"])[:2]
	if len(multi_targets) >= 1:
		daily_multi["raw_counts"][multi_targets[0]] = 3
	if len(multi_targets) >= 2:
		daily_multi["raw_counts"][multi_targets[1]] = 2
	daily_multi["raw_photo_count"] = sum(daily_multi["raw_counts"].values())

	weekly_current = build_weekly_summary(config, db_path, current_week)
	weekly_previous = build_weekly_summary(config, db_path, previous_week)

	return [
		(
			"[정정 안내]\n"
			"앞서 보낸 4분할 W16 수정본은 요청 의도와 달랐습니다.\n"
			"이번에는 요청하신 기준대로 포맷 검증용 4건만 다시 보냅니다:\n"
			"1) Daily 기본  2) Daily 다회 업로드 예시  3) Weekly 진행중 주  4) Weekly 완료된 지난주\n\n"
			"— AI 자동 송신 (HT 운동 인증 카운터)"
		),
		render_daily_message(config, daily_normal),
		render_daily_message(config, daily_multi),
		render_weekly_message(config, weekly_current),
		render_weekly_message(config, weekly_previous),
		(
			"[정정 완료]\n"
			"요청 기준인 데일리 2건 + 위클리 2건 포맷 검증 메시지 재전송을 완료했습니다.\n"
			"앞선 4분할 W16 수정본과는 별개로, 이번 4건이 최종 확인본입니다.\n\n"
			"— AI 자동 송신 (HT 운동 인증 카운터)"
		),
	]


def main():
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("--config", default="config.json")
	parser.add_argument("--output", required=True, help="생성할 JSON 파일 경로")
	parser.add_argument("--daily-date", default="2026-04-15")
	parser.add_argument("--current-week", default="2026-W16")
	parser.add_argument("--previous-week", default="2026-W15")
	args = parser.parse_args()

	config = load_json(Path(args.config))
	messages = build_messages(config, args.daily_date, args.current_week, args.previous_week)
	out_path = Path(args.output)
	out_path.write_text(json.dumps({"messages": messages}, ensure_ascii=False, indent=2), encoding="utf-8")
	print(out_path)
	print(f"messages={len(messages)}")


if __name__ == "__main__":
	main()

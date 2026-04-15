#!/usr/bin/env python3
"""HT 운동 인증 카운터 — 메인 오케스트레이터"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from collector import collect_messages, CollectionError
from counter import (
	count_verifications,
	weekly_summary,
	generate_msg_hash,
	parse_datetime,
	build_user_map,
)
from notifier import send_daily_report, send_weekly_report, send_error_alert
from storage import Storage
from sync_members import fetch_members_from_db

KST = ZoneInfo("Asia/Seoul")

log_dir = PROJECT_ROOT / "logs"
log_dir.mkdir(exist_ok=True)
data_dir = PROJECT_ROOT / "data"
data_dir.mkdir(exist_ok=True)

logging.basicConfig(
	level=logging.INFO,
	format="%(asctime)s [%(levelname)s] %(message)s",
	handlers=[
		logging.FileHandler(log_dir / "counter.log"),
		logging.StreamHandler(),
	],
)
logger = logging.getLogger(__name__)


def ensure_kakaotalk_running():
	"""카카오톡 앱이 실행 중인지 확인, 아니면 실행"""
	import subprocess
	import time

	result = subprocess.run(["pgrep", "-x", "KakaoTalk"], capture_output=True)
	if result.returncode != 0:
		logger.info("카카오톡 실행 중이 아님 → 자동 실행")
		subprocess.run(["open", "-a", "KakaoTalk"])
		time.sleep(10)


def _admin_target(config: dict) -> str | int:
	"""운영진 전송 대상 — admin_chat_id 우선, fallback으로 admin_chat_name."""
	return config.get("admin_chat_id") or config.get("admin_chat_name", "")


def main():
	config_path = PROJECT_ROOT / "config.json"
	with open(config_path, encoding="utf-8") as f:
		config = json.load(f)

	db_path = config["db_path"]
	if not os.path.isabs(db_path):
		db_path = str(PROJECT_ROOT / db_path)

	db = Storage(db_path)
	now = datetime.now(KST)
	today = now.strftime("%Y-%m-%d")
	week_key = now.strftime("%G-W%V")
	is_sunday = now.weekday() == 6
	admin_target = _admin_target(config)
	dry_run = config.get("dry_run", True)

	logger.info(f"=== 집계 시작: {today} (week: {week_key}) ===")

	try:
		ensure_kakaotalk_running()

		logger.info("메시지 수집 중...")
		messages = collect_messages(
			chat_id=config.get("chat_id"),
			chat_name=config.get("chat_name"),
			since="1d",
		)
		logger.info(f"수집된 메시지: {len(messages)}건")

		if not messages:
			logger.warning("수집된 메시지가 0건 — 빈 결과 그대로 저장하지 않음")
			db.log_run("daily", "warning", "수집 메시지 0건")
			send_error_alert(
				admin_target,
				f"{today} 수집 결과 0건. DB 동기화 상태 확인 필요.",
				config,
				dry_run,
			)
			return

		logger.info("인증 집계 중...")
		daily = count_verifications(messages, config)
		logger.info(
			f"집계 결과: capped={daily['capped_count']}, raw_photo={daily['raw_photo_count']}"
		)

		user_map = build_user_map(config.get("members", []))
		for msg in messages:
			if msg.get("type") != config.get("photo_message_type", "photo"):
				continue
			sender_id = msg.get("sender_id")
			canonical = user_map.get(sender_id, f"unknown_{sender_id}")
			timestamp = msg.get("timestamp", "")
			msg_type = msg.get("type", "")
			msg_hash = generate_msg_hash(sender_id, timestamp, msg_type)
			dt = parse_datetime(timestamp)
			if dt is None:
				continue
			date_key = dt.strftime("%Y-%m-%d")
			db.insert_verification(
				msg_hash,
				canonical,
				timestamp,
				date_key,
				week_key,
				str(msg.get("id", "")),
			)

		db.save_daily_summary(today, daily)
		db.log_run("daily", "success", f"{daily['capped_count']}건 집계")

		send_daily_report(admin_target, daily, config, dry_run)

		if is_sunday:
			logger.info("주간 요약 생성 중...")
			member_timestamps = db.get_week_member_timestamps(week_key)

			# 현재 인증방에 있는 멤버만 리포트에 포함.
			# since_days=28: 최근 4주 안에 메시지를 보낸 적 있는 user_id 집합.
			# 방을 나간 사람은 자연스럽게 빠진다.
			active_canonical = None
			try:
				active_list = fetch_members_from_db(config["chat_id"], since_days=28)
				active_ids = {m["user_id"] for m in active_list}
				active_canonical = [
					m["canonical"]
					for m in config.get("members", [])
					if m.get("user_id") in active_ids
				]
				logger.info(
					f"활성 멤버 {len(active_canonical)}/{len(config.get('members', []))}명 "
					f"(최근 28일 기준)"
				)
			except Exception as e:
				logger.warning(f"활성 멤버 조회 실패, 전체 members 사용: {e}")

			week_sum = weekly_summary(member_timestamps, config, active_members=active_canonical)
			db.save_weekly_summary(week_key, week_sum)

			send_weekly_report(admin_target, week_sum, config, dry_run)
			db.log_run("weekly", "success", f"week {week_key}")
			logger.info(f"주간 요약 완료: 멤버 {len(week_sum['members'])}명")

		logger.info("=== 집계 완료 ===")

	except CollectionError as e:
		logger.error(f"수집 실패: {e}")
		db.log_run("daily", "error", str(e))
		send_error_alert(admin_target, f"수집 실패: {e}", config, dry_run)
	except Exception as e:
		logger.error(f"예기치 않은 오류: {e}", exc_info=True)
		db.log_run("daily", "error", str(e))
		send_error_alert(admin_target, f"시스템 오류: {e}", config, dry_run)
	finally:
		db.close()


if __name__ == "__main__":
	main()

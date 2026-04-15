"""SQLite 기반 집계 데이터 저장 — 멱등성 보장"""
from __future__ import annotations

import sqlite3
import json
from datetime import datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS verifications (
	id INTEGER PRIMARY KEY AUTOINCREMENT,
	msg_hash TEXT UNIQUE,
	member_name TEXT NOT NULL,
	msg_datetime TEXT NOT NULL,
	date_key TEXT NOT NULL,
	week_key TEXT NOT NULL,
	log_id TEXT,
	created_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS daily_summaries (
	id INTEGER PRIMARY KEY AUTOINCREMENT,
	date_key TEXT UNIQUE,
	summary_json TEXT NOT NULL,
	raw_photo_count INTEGER,
	capped_count INTEGER,
	flagged_json TEXT,
	created_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS weekly_summaries (
	id INTEGER PRIMARY KEY AUTOINCREMENT,
	week_key TEXT UNIQUE,
	summary_json TEXT NOT NULL,
	created_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS run_log (
	id INTEGER PRIMARY KEY AUTOINCREMENT,
	run_type TEXT NOT NULL,
	status TEXT NOT NULL,
	message TEXT,
	created_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_verifications_week ON verifications(week_key);
CREATE INDEX IF NOT EXISTS idx_verifications_date ON verifications(date_key);
CREATE INDEX IF NOT EXISTS idx_verifications_member ON verifications(member_name);
"""


class Storage:
	def __init__(self, db_path: str):
		self.conn = sqlite3.connect(db_path)
		self.conn.row_factory = sqlite3.Row
		self.conn.executescript(SCHEMA)

	def insert_verification(self, msg_hash: str, member: str,
							msg_dt: str, date_key: str, week_key: str,
							log_id: str = None) -> bool:
		"""인증 기록 삽입. 중복이면 무시하고 False 반환."""
		try:
			self.conn.execute(
				"INSERT OR IGNORE INTO verifications "
				"(msg_hash, member_name, msg_datetime, date_key, week_key, log_id) "
				"VALUES (?, ?, ?, ?, ?, ?)",
				(msg_hash, member, msg_dt, date_key, week_key, log_id)
			)
			self.conn.commit()
			return self.conn.total_changes > 0
		except sqlite3.Error:
			return False

	def save_daily_summary(self, date_key: str, summary: dict):
		"""일별 요약 저장 (REPLACE — 재집계 시 덮어쓰기)"""
		self.conn.execute(
			"INSERT OR REPLACE INTO daily_summaries "
			"(date_key, summary_json, raw_photo_count, capped_count, flagged_json) "
			"VALUES (?, ?, ?, ?, ?)",
			(
				date_key,
				json.dumps(summary.get("counts", {}), ensure_ascii=False),
				summary.get("raw_photo_count", 0),
				summary.get("capped_count", 0),
				json.dumps(summary.get("flagged", []), ensure_ascii=False)
			)
		)
		self.conn.commit()

	def save_weekly_summary(self, week_key: str, summary: dict):
		self.conn.execute(
			"INSERT OR REPLACE INTO weekly_summaries "
			"(week_key, summary_json) VALUES (?, ?)",
			(week_key, json.dumps(summary, ensure_ascii=False))
		)
		self.conn.commit()

	def get_week_daily_records(self, week_key: str) -> list[dict]:
		"""특정 주의 일별 기록 조회"""
		rows = self.conn.execute(
			"SELECT * FROM daily_summaries WHERE date_key IN "
			"(SELECT DISTINCT date_key FROM verifications WHERE week_key = ?)",
			(week_key,)
		).fetchall()

		result = []
		for row in rows:
			result.append({
				"date_key": row["date_key"],
				"counts": json.loads(row["summary_json"]),
				"raw_photo_count": row["raw_photo_count"],
				"capped_count": row["capped_count"]
			})
		return result

	def get_member_week_count(self, member: str, week_key: str) -> int:
		"""멤버의 주간 인증 횟수 (일일 캡 적용 — 고유 날짜 수)"""
		row = self.conn.execute(
			"SELECT COUNT(DISTINCT date_key) as cnt "
			"FROM verifications WHERE member_name = ? AND week_key = ?",
			(member, week_key)
		).fetchone()
		return row["cnt"] if row else 0

	def log_run(self, run_type: str, status: str, message: str = ""):
		self.conn.execute(
			"INSERT INTO run_log (run_type, status, message) VALUES (?, ?, ?)",
			(run_type, status, message)
		)
		self.conn.commit()

	def close(self):
		self.conn.close()

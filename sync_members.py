#!/usr/bin/env python3
"""config.json의 members 배열을 kakaocli로 자동 동기화.

카카오톡 Mac 로컬 DB에서 config.chat_id 인증방에 메시지를 보낸 적 있는
모든 유저(=사실상 활성 멤버)를 추출해 config.json에 반영한다.

기본 동작:
	- 기존 멤버의 canonical / aliases는 보존
	- displayName이 기존 aliases에 없으면 추가
	- 새 user_id는 새 엔트리로 추가 (canonical = 정규화된 displayName)
	- 채팅방에서 사라진 user_id는 그대로 유지 (과거 집계 이력 보호)

옵션:
	--prune      현재 채팅방에 없는 기존 멤버를 제거
	--dry-run    변경 없이 diff만 출력
	--config     대상 config 경로 (기본: ./config.json)

Usage:
	python3 sync_members.py
	python3 sync_members.py --dry-run
	python3 sync_members.py --prune
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

CANONICAL_SUFFIX_RE = re.compile(r"\.\d{2,4}$")


def fetch_members_from_db(chat_id: int, since_days: int = 0) -> list[dict]:
	"""kakaocli query로 chat_id의 distinct 참여자 추출.

	Args:
		chat_id: 대상 채팅방 id (NTChatMessage.chatId)
		since_days: 0이면 전체 기간 메시지 이력 대상. 양수면 최근 N일 동안
			1회 이상 메시지를 보낸 사람만. 방을 나간 멤버를 걸러내는 용도.
	"""
	where_clauses = [f"m.chatId = {int(chat_id)}", "m.authorId > 0"]
	if since_days > 0:
		where_clauses.append(
			f"m.sentAt >= cast(strftime('%s', 'now', '-{int(since_days)} days') as integer)"
		)

	sql = f"""
	SELECT DISTINCT
		m.authorId,
		COALESCE(mp.displayName, u.nickName, u.displayName) AS name
	FROM NTChatMessage m
	LEFT JOIN NTMultiProfile mp
		ON mp.userId = m.authorId AND mp.linkId = 0
	LEFT JOIN NTUser u
		ON u.userId = m.authorId AND u.linkId = 0
	WHERE {' AND '.join(where_clauses)}
	ORDER BY m.authorId
	"""

	result = subprocess.run(
		["kakaocli", "query", sql],
		capture_output=True,
		text=True,
		timeout=60,
	)

	if result.returncode != 0:
		raise RuntimeError(f"kakaocli query 실패: {result.stderr.strip()}")

	rows = json.loads(result.stdout)
	members = []
	for row in rows:
		user_id, name = row[0], row[1]
		if user_id is None or not name:
			continue
		members.append({"user_id": int(user_id), "name": str(name).strip()})
	return members


def normalize_canonical(name: str) -> str:
	"""닉네임 뒤의 연도 접미사(.90, .2001) 제거."""
	return CANONICAL_SUFFIX_RE.sub("", name).strip() or name


def merge_members(existing: list[dict], fetched: list[dict], prune: bool) -> tuple[list[dict], dict]:
	"""기존 members와 DB 멤버 병합.

	Returns:
		(merged_members, change_report)
	"""
	by_id: dict[int, dict] = {}
	for m in existing:
		if "user_id" in m:
			by_id[int(m["user_id"])] = {
				"user_id": int(m["user_id"]),
				"canonical": m.get("canonical", ""),
				"aliases": list(m.get("aliases", [])),
			}

	added: list[dict] = []
	updated: list[dict] = []
	alias_added: list[tuple[str, str]] = []

	fetched_ids: set[int] = set()
	for f in fetched:
		uid = f["user_id"]
		name = f["name"]
		fetched_ids.add(uid)

		if uid in by_id:
			entry = by_id[uid]
			if name not in entry["aliases"]:
				entry["aliases"].append(name)
				alias_added.append((entry["canonical"] or str(uid), name))
				updated.append(entry)
		else:
			canonical = normalize_canonical(name)
			new_entry = {
				"user_id": uid,
				"canonical": canonical,
				"aliases": [name] if name != canonical else [canonical],
			}
			by_id[uid] = new_entry
			added.append(new_entry)

	removed: list[dict] = []
	if prune:
		stale_ids = [uid for uid in by_id if uid not in fetched_ids]
		for uid in stale_ids:
			removed.append(by_id.pop(uid))

	merged = sorted(by_id.values(), key=lambda m: m["user_id"])
	report = {
		"added": added,
		"removed": removed,
		"updated": updated,
		"alias_added": alias_added,
		"fetched": len(fetched),
		"existing": len(existing),
		"final": len(merged),
	}
	return merged, report


def print_report(report: dict, prune: bool):
	print(f"수집된 멤버: {report['fetched']}명")
	print(f"기존 멤버:   {report['existing']}명")
	print(f"최종 멤버:   {report['final']}명")
	print()

	if report["added"]:
		print(f"[+] 새 멤버 {len(report['added'])}명:")
		for m in report["added"]:
			print(f"    + {m['user_id']}  {m['canonical']} ({', '.join(m['aliases'])})")

	if report["alias_added"]:
		print(f"[~] alias 추가 {len(report['alias_added'])}건:")
		for canonical, new_alias in report["alias_added"]:
			print(f"    ~ {canonical}: + {new_alias!r}")

	if report["removed"]:
		label = "제거" if prune else "사라짐(보존)"
		print(f"[-] {label} {len(report['removed'])}명:")
		for m in report["removed"]:
			print(f"    - {m['user_id']}  {m.get('canonical', '?')}")

	if not (report["added"] or report["alias_added"] or report["removed"]):
		print("변경 없음")


def main():
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("--config", default="config.json")
	parser.add_argument("--dry-run", action="store_true")
	parser.add_argument("--prune", action="store_true")
	args = parser.parse_args()

	config_path = Path(args.config)
	if not config_path.exists():
		print(f"ERROR: {config_path} 없음", file=sys.stderr)
		sys.exit(1)

	with open(config_path, encoding="utf-8") as f:
		config = json.load(f)

	chat_id = config.get("chat_id")
	if not chat_id:
		print("ERROR: config.chat_id 누락", file=sys.stderr)
		sys.exit(1)

	print(f"chat_id={chat_id} ({config.get('chat_name', '?')})")
	fetched = fetch_members_from_db(chat_id)
	merged, report = merge_members(
		existing=config.get("members", []),
		fetched=fetched,
		prune=args.prune,
	)

	print_report(report, args.prune)

	if args.dry_run:
		print("\n--dry-run: config.json 변경하지 않음")
		return

	if not (report["added"] or report["alias_added"] or (args.prune and report["removed"])):
		print("\n변경사항 없음 — 저장 생략")
		return

	backup = config_path.with_suffix(config_path.suffix + ".bak")
	shutil.copy2(config_path, backup)

	config["members"] = merged
	with open(config_path, "w", encoding="utf-8") as f:
		json.dump(config, f, ensure_ascii=False, indent=2)

	print(f"\n저장 완료: {config_path}")
	print(f"백업:     {backup}")


if __name__ == "__main__":
	main()

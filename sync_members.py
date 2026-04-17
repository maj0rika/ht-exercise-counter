#!/usr/bin/env python3
"""config.json의 members 배열을 kakaocli로 자동 동기화.

카카오톡 Mac 로컬 DB에서 인증방(config.chat_id)의 현재 멤버를 긁어와서
4가지 이름(친구별명/멀티프로필/본인닉/displayName)을 모두 수집하고,
canonical은 아래 규칙으로 결정한다.

Canonical 결정 규칙 (우선순위):
	1) NTUser.friendNickName(내가 친구로 저장한 별명)에 한글 3글자 이상 연속
	   → 친구별명 채택. "bhsn 송치성"처럼 풀네임 포함이면 별명이 우선.
	2) 본인 닉(NTUser.nickName)에 한글 3글자 이상 연속 → 본인 닉 채택.
	   "석두" "♡귀금당♡" 같은 별명성 친구별명을 쓴 경우 이 가지로 빠짐.
	3) 그래도 없으면 멀티프로필 → displayName → unknown_{id} 순으로 fallback
	   (이 케이스는 --interactive 모드에서 관리자 확인 대상으로 표시).

기존 동작:
	- 기본값은 기존 canonical 보존 (aliases만 누적). --reconcile 시 재평가.
	- 새 user_id는 새 엔트리로 추가
	- 방을 나간 user_id는 그대로 유지 (과거 집계 이력 보호, --prune으로만 제거)

옵션:
	--reconcile     기존 canonical도 friendNickName/nickName 규칙으로 재평가.
	                certain=True면 자동 교체, certain=False면 --interactive와 조합해야
	                관리자에게 질문 (아니면 기존 canonical 유지).
	--interactive   자동 판정이 애매한 멤버를 관리자에게 물어봄
	--prune         현재 채팅방에 없는 기존 멤버를 제거
	--dry-run       변경 없이 diff만 출력
	--config        대상 config 경로 (기본: ./config.json)

Usage:
	python3 sync_members.py
	python3 sync_members.py --reconcile --interactive  # 첫 세팅 권장
	python3 sync_members.py --dry-run --reconcile
	python3 sync_members.py --prune
"""
from __future__ import annotations

import argparse
import json
import plistlib
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path


def _run_kakaocli_query(sql: str, label: str, timeout: int = 30, max_retries: int = 6) -> str:
	"""kakaocli query 실행 + DB 락 자동 재시도 + 진행 상황 출력.

	카카오톡이 write 중이면 "file is not a database" / "database is locked"
	에러가 뜨는데, 잠깐 기다리면 풀리므로 지수 백오프로 재시도한다.
	"""
	print(f"  [조회] {label} ...", end="", flush=True)
	last_err = ""
	for attempt in range(1, max_retries + 1):
		result = subprocess.run(
			["kakaocli", "query", sql],
			capture_output=True, text=True, timeout=timeout,
		)
		if result.returncode == 0:
			if attempt > 1:
				print(f" ok (재시도 {attempt-1}회)", flush=True)
			else:
				print(" ok", flush=True)
			return result.stdout
		last_err = result.stderr.strip()
		if any(tok in last_err for tok in ("file is not a database", "database is locked", "disk I/O error")):
			print(f" 잠김 — 대기 {attempt}/{max_retries}", end="", flush=True)
			time.sleep(0.5 * attempt)
			continue
		break
	print(" 실패", flush=True)
	raise RuntimeError(f"kakaocli query 실패 ({label}): {last_err}")

CANONICAL_SUFFIX_RE = re.compile(r"\.\d{2,4}$")
HANGUL_3_PLUS = re.compile(r"[가-힣]{3,}")

# 풀네임이 아닐 가능성이 높은 별명 패턴.
# 여기 걸리면 pick_canonical이 certain=False로 분류하고 --interactive에서 질문.
HONORIFIC_TAIL_RE = re.compile(r"(형|누나|언니|오빠|씨|님)$")
SHORT_NAME_PLUS_YI_RE = re.compile(r"^[가-힣]{1,2}이$")  # "우석이" "상인이" 같은 별명
REPEAT_CHUNK_RE = re.compile(r"(.{2,})\1")              # "열형열형" "우영우영"


def looks_suspicious_as_fullname(name: str) -> bool:
	"""한글 3+ 매치는 되지만 풀네임일 가능성이 낮은 별명을 골라낸다."""
	if not name:
		return True
	s = name.strip()
	if "(" in s or ")" in s:
		return True
	if HONORIFIC_TAIL_RE.search(s):
		return True
	if SHORT_NAME_PLUS_YI_RE.match(s):
		return True
	if REPEAT_CHUNK_RE.search(s):
		return True
	return False

NAME_KEYS = ("friend_nick", "mp_display", "self_nick", "disp_name")
NAME_LABELS = {
	"friend_nick": "친구별명(내가 저장)",
	"mp_display":  "멀티프로필",
	"self_nick":   "본인 닉네임",
	"disp_name":   "displayName",
}


def fetch_room_member_ids(chat_id: int) -> list[int]:
	"""현재 채팅방의 실제 멤버 user_id 리스트 (NTChatRoom.watermarkKeys bplist 기반)."""
	sql = f"SELECT hex(watermarkKeys) FROM NTChatRoom WHERE chatId = {int(chat_id)}"
	stdout = _run_kakaocli_query(sql, f"방 멤버 user_id (chat_id={chat_id})")
	rows = json.loads(stdout or "[]")
	if not rows or not rows[0] or not rows[0][0]:
		return []
	blob = bytes.fromhex(rows[0][0])
	data = plistlib.loads(blob)
	if not isinstance(data, list):
		return []
	return [int(x) for x in data if isinstance(x, int)]


def fetch_enriched_names(user_ids) -> dict[int, dict]:
	"""user_id → {friend_nick, mp_display, self_nick, disp_name} (strip된 값, 없으면 '')."""
	user_ids = list(user_ids)
	if not user_ids:
		return {}
	ids_csv = ",".join(str(int(u)) for u in user_ids)
	sql = f"""
	SELECT u.userId,
	       COALESCE(u.friendNickName, '') AS friend_nick,
	       COALESCE(mp.displayName, '')    AS mp_display,
	       COALESCE(u.nickName, '')        AS self_nick,
	       COALESCE(u.displayName, '')     AS disp_name
	FROM NTUser u
	LEFT JOIN NTMultiProfile mp
		ON mp.userId = u.userId AND mp.linkId = 0
	WHERE u.userId IN ({ids_csv}) AND u.linkId = 0
	"""
	stdout = _run_kakaocli_query(sql, f"{len(user_ids)}명 이름 4종(친구별명/멀티프로필/본인닉/displayName)")
	rows = json.loads(stdout or "[]")
	out: dict[int, dict] = {}
	for row in rows:
		uid = int(row[0])
		out[uid] = {
			"friend_nick": (row[1] or "").strip(),
			"mp_display":  (row[2] or "").strip(),
			"self_nick":   (row[3] or "").strip(),
			"disp_name":   (row[4] or "").strip(),
		}
	for uid in user_ids:
		out.setdefault(int(uid), {k: "" for k in NAME_KEYS})
	return out


def pick_canonical(names: dict) -> tuple[str, bool]:
	"""
	4가지 이름에서 canonical 결정.
	Returns (canonical, certain). certain=False면 한글 3+ 풀네임을 어디서도 못 찾음.
	"""
	fn = names.get("friend_nick", "")
	sn = names.get("self_nick",   "")
	mp = names.get("mp_display",  "")
	dn = names.get("disp_name",   "")

	# 1) 친구별명에 한글 3+ 매치 AND 호칭/반복/괄호 등 별명 패턴 아님 → 친구별명
	if fn and HANGUL_3_PLUS.search(fn) and not looks_suspicious_as_fullname(fn):
		return fn, True
	# 2) 본인 닉도 같은 조건 → 본인 닉
	if sn and HANGUL_3_PLUS.search(sn) and not looks_suspicious_as_fullname(sn):
		return sn, True
	# 3) 멀티프로필, displayName
	if mp and HANGUL_3_PLUS.search(mp) and not looks_suspicious_as_fullname(mp):
		return mp, True
	if dn and HANGUL_3_PLUS.search(dn) and not looks_suspicious_as_fullname(dn):
		return dn, True
	# 4) 확신 불가 — 본인닉>친구별명>mp>disp 순 fallback, certain=False
	candidate = sn or fn or mp or dn
	return candidate, False


def collect_aliases(names: dict) -> list[str]:
	"""4가지 이름 중 비어있지 않고 서로 다른 것만 수집 (입력 순서 유지)."""
	seen: list[str] = []
	for key in NAME_KEYS:
		n = (names.get(key) or "").strip()
		if n and n not in seen:
			seen.append(n)
	return seen


def fetch_user_names(user_ids) -> dict:
	"""
	user_id → canonical. main.py의 주간 리포트 active_canonical 계산에 쓰인다.
	새 규칙: pick_canonical과 동일. 미결정이면 'unknown_{id}'.
	"""
	enriched = fetch_enriched_names(user_ids)
	mapping: dict[int, str] = {}
	for uid, names in enriched.items():
		canonical, _ = pick_canonical(names)
		mapping[int(uid)] = canonical or f"unknown_{uid}"
	return mapping


def normalize_canonical(name: str) -> str:
	"""닉네임 뒤의 연도 접미사(.90, .2001) 제거 — 레거시 데이터 호환용."""
	return CANONICAL_SUFFIX_RE.sub("", name).strip() or name


def resolve_via_interview(pending: list, by_id: dict):
	"""자동 판정 실패 멤버를 stdin으로 확인."""
	print(f"\n=== 관리자 확인 필요: {len(pending)}명 ===")
	print("(한글 3글자 이상 풀네임을 자동 판정 못함)\n")
	for idx, (uid, names, auto_pick) in enumerate(pending, 1):
		print(f"[{idx}/{len(pending)}] user_id={uid}")
		for slot, key in enumerate(NAME_KEYS, 1):
			val = names.get(key) or "(없음)"
			print(f"  {slot}) {NAME_LABELS[key]:<18}: {val}")
		print(f"  → 자동추천: {auto_pick or '(없음)'}")
		try:
			choice = input("  [1/2/3/4 / 직접입력 / 엔터=추천 / s=스킵]: ").strip()
		except EOFError:
			print("  입력 종료 — 나머지 스킵")
			break

		if choice == "s":
			print()
			continue

		if choice in ("1", "2", "3", "4"):
			name = names[NAME_KEYS[int(choice) - 1]]
		elif choice == "":
			name = auto_pick
		else:
			name = choice

		name = (name or "").strip()
		if not name:
			print("  (빈 값 — 스킵)\n")
			continue

		by_id[uid]["canonical"] = name
		if name not in by_id[uid]["aliases"]:
			by_id[uid]["aliases"].append(name)
		print(f"  → '{name}'으로 확정\n")


def merge_members(
	existing: list[dict],
	enriched_by_id: dict,
	prune: bool,
	interactive: bool = False,
	reconcile: bool = False,
) -> tuple[list[dict], dict]:
	"""기존 members와 방 멤버 4가지 이름을 병합.

	reconcile=False (기본): 기존 canonical은 보존, aliases만 누적.
	reconcile=True: 기존 canonical도 pick_canonical로 재평가.
		- certain=True이고 현재와 다름 → 자동 교체 (replaced에 기록)
		- certain=False이고 현재 canonical도 한글 3+ 풀네임 아님
		  → interactive 모드면 관리자에게 질문, 아니면 현상 유지
	"""
	by_id: dict[int, dict] = {}
	existing_ids: set[int] = set()
	for m in existing:
		if "user_id" in m:
			uid = int(m["user_id"])
			existing_ids.add(uid)
			by_id[uid] = {
				"user_id":   uid,
				"canonical": (m.get("canonical") or "").strip(),
				"aliases":   list(m.get("aliases", [])),
			}

	added: list[dict] = []
	alias_added: list[tuple[str, str]] = []
	replaced: list[tuple[int, str, str]] = []
	pending: list[tuple[int, dict, str]] = []

	for uid, names in enriched_by_id.items():
		new_aliases = collect_aliases(names)

		if uid in existing_ids:
			entry = by_id[uid]
			for a in new_aliases:
				if a and a not in entry["aliases"]:
					entry["aliases"].append(a)
					alias_added.append((entry["canonical"] or str(uid), a))

			if reconcile:
				suggested, certain = pick_canonical(names)
				current = entry["canonical"]
				if certain and suggested and suggested != current:
					replaced.append((uid, current, suggested))
					entry["canonical"] = suggested
					if suggested not in entry["aliases"]:
						entry["aliases"].append(suggested)
					if current and current not in entry["aliases"]:
						entry["aliases"].append(current)
				elif not certain and (
					not HANGUL_3_PLUS.search(current)
					or looks_suspicious_as_fullname(current)
				):
					pending.append((uid, names, suggested or current))
		else:
			canonical, certain = pick_canonical(names)
			canonical = canonical or f"unknown_{uid}"
			new_entry = {
				"user_id":   uid,
				"canonical": canonical,
				"aliases":   new_aliases or [canonical],
			}
			by_id[uid] = new_entry
			added.append(new_entry)
			if not certain:
				pending.append((uid, names, canonical))

	print(
		f"  → 자동 교체 {len(replaced)}명 / 새 멤버 {len(added)}명 / "
		f"alias 추가 {len(alias_added)}건 / 관리자 확인 {len(pending)}명",
		flush=True,
	)

	if interactive and pending:
		resolve_via_interview(pending, by_id)

	removed: list[dict] = []
	if prune:
		stale_ids = [uid for uid in by_id if uid not in enriched_by_id]
		for uid in stale_ids:
			removed.append(by_id.pop(uid))

	merged = sorted(by_id.values(), key=lambda m: m["user_id"])
	report = {
		"added":       added,
		"removed":     removed,
		"alias_added": alias_added,
		"replaced":    replaced,
		"pending":     pending,
		"fetched":     len(enriched_by_id),
		"existing":    len(existing),
		"final":       len(merged),
	}
	return merged, report


def print_report(report: dict, prune: bool):
	print(f"방에서 수집: {report['fetched']}명")
	print(f"기존 멤버:   {report['existing']}명")
	print(f"최종 멤버:   {report['final']}명")
	if report.get("pending"):
		print(
			f"애매한 케이스: {len(report['pending'])}명 "
			"(한글 풀네임 판정 실패 — --interactive 로 재확인 권장)"
		)
	print()

	if report["added"]:
		print(f"[+] 새 멤버 {len(report['added'])}명:")
		for m in report["added"]:
			print(f"    + {m['user_id']}  {m['canonical']} ({', '.join(m['aliases'])})")

	if report.get("replaced"):
		print(f"[*] canonical 교체 {len(report['replaced'])}명 (reconcile):")
		for uid, old, new in report["replaced"]:
			print(f"    * {uid}  '{old}' → '{new}'")

	if report["alias_added"]:
		print(f"[~] alias 추가 {len(report['alias_added'])}건:")
		for canonical, new_alias in report["alias_added"]:
			print(f"    ~ {canonical}: + {new_alias!r}")

	if report["removed"]:
		label = "제거" if prune else "사라짐(보존)"
		print(f"[-] {label} {len(report['removed'])}명:")
		for m in report["removed"]:
			print(f"    - {m['user_id']}  {m.get('canonical', '?')}")

	if not (report["added"] or report.get("replaced") or report["alias_added"] or report["removed"]):
		print("변경 없음")


def main():
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("--config", default="config.json")
	parser.add_argument("--dry-run", action="store_true")
	parser.add_argument("--prune", action="store_true")
	parser.add_argument(
		"--interactive",
		action="store_true",
		help="자동 판정이 애매한 멤버를 관리자에게 물어봄 (첫 세팅 권장)",
	)
	parser.add_argument(
		"--reconcile",
		action="store_true",
		help="기존 canonical도 friendNickName/nickName 규칙으로 재평가",
	)
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
	flags = []
	if args.reconcile: flags.append("--reconcile")
	if args.interactive: flags.append("--interactive")
	if args.prune: flags.append("--prune")
	if args.dry_run: flags.append("--dry-run")
	print(f"모드: {' '.join(flags) if flags else '(alias만 누적)'}")
	print()

	room_ids = fetch_room_member_ids(chat_id)
	if not room_ids:
		print("ERROR: 방 멤버를 가져오지 못함 (카카오톡 DB 확인 필요)", file=sys.stderr)
		sys.exit(1)
	print(f"→ 방 멤버 user_id: {len(room_ids)}명")

	enriched = fetch_enriched_names(room_ids)
	print(f"→ 이름 조회 완료: {len(enriched)}명\n")
	print("[분석] 이름 매칭 규칙 적용 중...", flush=True)

	merged, report = merge_members(
		existing=config.get("members", []),
		enriched_by_id=enriched,
		prune=args.prune,
		interactive=args.interactive,
		reconcile=args.reconcile,
	)

	print_report(report, args.prune)

	if args.dry_run:
		print("\n--dry-run: config.json 변경하지 않음")
		return

	has_changes = bool(
		report["added"]
		or report["alias_added"]
		or report.get("replaced")
		or (args.prune and report["removed"])
		or (args.interactive and report["pending"])
	)
	if not has_changes:
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

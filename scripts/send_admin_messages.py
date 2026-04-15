#!/usr/bin/env python3
"""운영진방에 여러 메시지를 한 번에 전송한다.

메시지들은 JSON 파일에서 읽는다:
{
  "messages": ["첫 메시지", "둘째 메시지", "..."]
}
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from notifier import send_messages_batch


def load_json(path: Path) -> dict:
	with path.open(encoding="utf-8") as f:
		return json.load(f)


def main():
	logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("--config", default="config.json")
	parser.add_argument("--messages", required=True, help="메시지 배열이 들어있는 JSON 파일")
	parser.add_argument("--dry-run", action="store_true")
	parser.add_argument("--force-send", action="store_true", help="config.json의 dry_run=true를 무시하고 실제 전송")
	args = parser.parse_args()

	config_path = Path(args.config)
	messages_path = Path(args.messages)

	config = load_json(config_path)
	payload = load_json(messages_path)
	messages = payload.get("messages", [])
	admin_chat = config.get("admin_chat_id") or config.get("admin_chat_name", "")
	effective_dry_run = False if args.force_send else (args.dry_run or config.get("dry_run", True))

	try:
		send_messages_batch(admin_chat, messages, config, dry_run=effective_dry_run)
	except Exception as exc:
		print(str(exc), file=sys.stderr)
		sys.exit(1)


if __name__ == "__main__":
	main()

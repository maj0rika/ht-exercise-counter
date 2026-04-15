from __future__ import annotations

import unittest

import notifier


class NotifierCommandTests(unittest.TestCase):
	def test_ensure_chat_script_exists(self):
		self.assertTrue(notifier.ENSURE_CHAT_SCRIPT.exists())

	def test_format_case_builder_script_exists(self):
		self.assertTrue((notifier.SCRIPT_DIR / "build_format_case_messages.py").exists())

	def test_build_direct_send_cmd_with_chat_id(self):
		cmd = notifier._build_direct_send_cmd("chat_deadbeef")
		self.assertEqual(
			cmd,
			["kmsg", "send", "--keep-window", "--deep-recovery", "--chat-id", "chat_deadbeef"],
		)

	def test_build_direct_send_cmd_with_chat_name(self):
		cmd = notifier._build_direct_send_cmd("[#HT] 운영진방")
		self.assertEqual(
			cmd,
			["kmsg", "send", "--keep-window", "--deep-recovery", "[#HT] 운영진방"],
		)

	def test_resolve_open_chat_name_prefers_admin_chat_name_for_hash_id(self):
		name = notifier._resolve_open_chat_name(
			"chat_deadbeef",
			{"admin_chat_name": "[#HT] 운영진방"},
		)
		self.assertEqual(name, "[#HT] 운영진방")

	def test_build_open_chat_cmd_uses_kmsg_read_keep_window(self):
		cmd = notifier._build_open_chat_cmd(
			"chat_deadbeef",
			{"admin_chat_name": "[#HT] 운영진방"},
		)
		self.assertEqual(
			cmd,
			[
				"kmsg", "read", "[#HT] 운영진방",
				"--limit", "1",
				"--keep-window",
				"--deep-recovery",
			],
		)

	def test_build_open_chat_cmd_requires_name_for_hash_target(self):
		with self.assertRaises(ValueError):
			notifier._build_open_chat_cmd("chat_deadbeef", {})

	def test_send_messages_batch_dry_run(self):
		notifier.send_messages_batch(
			"chat_deadbeef",
			["one", "two"],
			{"admin_sender": "kmsg"},
			dry_run=True,
		)


if __name__ == "__main__":
	unittest.main()

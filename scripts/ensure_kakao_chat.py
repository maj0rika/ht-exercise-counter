#!/usr/bin/env python3
"""카카오톡 상태를 정리해서 특정 채팅창을 확보한다.

다루는 상태:
- 카카오톡이 안 열려 있는 경우
- 메인 창이 친구 목록/대화 목록/설정창 등에 머물러 있는 경우
- 대상 채팅창이 이미 열려 있는 경우
- 다른 채팅창이 앞에 떠 있는 경우

전략:
1. 대상 채팅창이 이미 있으면 그 창만 raise
2. 없으면 카카오톡 메인 창을 앞으로 올리고 Cmd+2로 '채팅' 탭 보장
3. 그 상태에서만 `kmsg read --keep-window`로 대상 채팅창 오픈
4. 마지막에 대상 채팅창을 다시 raise
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time

MAIN_WINDOW_TITLE = "카카오톡"

WINDOW_EXISTS_SCRIPT = """
on run argv
	set targetTitle to item 1 of argv
	tell application "System Events"
		tell process "KakaoTalk"
			if exists first window whose name is targetTitle then
				return "true"
			end if
		end tell
	end tell
	return "false"
end run
""".strip()

FOCUS_WINDOW_SCRIPT = """
on run argv
	set targetTitle to item 1 of argv
	tell application "KakaoTalk" to activate
	tell application "System Events"
		tell process "KakaoTalk"
			if not (exists first window whose name is targetTitle) then error "Window not found: " & targetTitle
			perform action "AXRaise" of (first window whose name is targetTitle)
		end tell
	end tell
end run
""".strip()

SWITCH_TO_CHAT_TAB_SCRIPT = """
tell application "KakaoTalk" to activate
tell application "System Events"
	tell process "KakaoTalk"
		keystroke "2" using command down
	end tell
end tell
""".strip()

FOCUS_MESSAGE_INPUT_SCRIPT = """
on run argv
	set targetTitle to item 1 of argv
	tell application "System Events"
		tell process "KakaoTalk"
			if name of front window is not targetTitle then
				error "Target chat is not front window: " & targetTitle
			end if
			if not (exists text area 1 of scroll area 2 of front window) then
				error "Message input not found: " & targetTitle
			end if
			set value of attribute "AXFocused" of text area 1 of scroll area 2 of front window to true
		end tell
	end tell
end run
""".strip()


def run_osascript(script: str, *args: str, timeout: int = 5) -> subprocess.CompletedProcess:
	cmd = ["osascript", "-e", script]
	if args:
		cmd.extend(["--", *args])
	return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def window_exists(title: str) -> bool:
	result = run_osascript(WINDOW_EXISTS_SCRIPT, title, timeout=5)
	return result.returncode == 0 and result.stdout.strip() == "true"


def wait_for_window(title: str, timeout: float = 10.0) -> bool:
	deadline = time.time() + timeout
	while time.time() < deadline:
		if window_exists(title):
			return True
		time.sleep(0.2)
	return False


def focus_window(title: str):
	result = run_osascript(FOCUS_WINDOW_SCRIPT, title, timeout=5)
	if result.returncode != 0:
		raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"failed to focus {title}")


def focus_message_input(title: str):
	result = run_osascript(FOCUS_MESSAGE_INPUT_SCRIPT, title, timeout=5)
	if result.returncode != 0:
		raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"failed to focus input of {title}")
	time.sleep(0.1)


def ensure_app_and_main_window():
	subprocess.run(["open", "-a", "KakaoTalk"], capture_output=True, text=True, timeout=10)
	run_osascript('tell application "KakaoTalk" to reopen', timeout=5)
	if not wait_for_window(MAIN_WINDOW_TITLE, timeout=15):
		raise RuntimeError("카카오톡 메인 창을 열지 못했습니다.")
	focus_window(MAIN_WINDOW_TITLE)


def switch_to_chat_tab():
	result = run_osascript(SWITCH_TO_CHAT_TAB_SCRIPT, timeout=5)
	if result.returncode != 0:
		raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "failed to switch to chat tab")
	time.sleep(0.4)


def open_chat_via_kmsg(chat_name: str):
	result = subprocess.run(
		["kmsg", "read", chat_name, "--limit", "1", "--keep-window", "--deep-recovery"],
		capture_output=True,
		text=True,
		timeout=60,
	)
	if result.returncode != 0:
		raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "kmsg read failed")


def ensure_chat(chat_name: str):
	if window_exists(chat_name):
		focus_window(chat_name)
		focus_message_input(chat_name)
		return

	ensure_app_and_main_window()
	switch_to_chat_tab()
	open_chat_via_kmsg(chat_name)

	if not wait_for_window(chat_name, timeout=15):
		raise RuntimeError(f"대상 채팅창이 열리지 않았습니다: {chat_name}")
	focus_window(chat_name)
	focus_message_input(chat_name)


def main():
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("--chat", required=True, help="열어둘 카카오톡 채팅창 제목")
	args = parser.parse_args()

	try:
		ensure_chat(args.chat)
	except Exception as exc:
		print(str(exc), file=sys.stderr)
		sys.exit(1)


if __name__ == "__main__":
	main()

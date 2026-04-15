#!/usr/bin/env bash
# launchd plist 설치 스크립트
# 템플릿의 __PROJECT_ROOT__ / __PYTHON__ 플레이스홀더를 실제 경로로 치환해
# ~/Library/LaunchAgents/에 설치하고 로드한다.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="$PROJECT_ROOT/launchd/com.ht.exercise-counter.plist.template"
OUTPUT="$PROJECT_ROOT/launchd/com.ht.exercise-counter.plist"
TARGET="$HOME/Library/LaunchAgents/com.ht.exercise-counter.plist"

PYTHON_PATH="${PYTHON_PATH:-$(command -v python3)}"

if [[ -z "$PYTHON_PATH" ]]; then
    echo "ERROR: python3을 찾을 수 없습니다. PYTHON_PATH 환경변수로 지정하세요." >&2
    exit 1
fi

if [[ ! -f "$TEMPLATE" ]]; then
    echo "ERROR: 템플릿 없음: $TEMPLATE" >&2
    exit 1
fi

echo "프로젝트 루트:    $PROJECT_ROOT"
echo "Python 인터프리터: $PYTHON_PATH"

# 템플릿 치환 (sed)
sed \
    -e "s|__PROJECT_ROOT__|$PROJECT_ROOT|g" \
    -e "s|__PYTHON__|$PYTHON_PATH|g" \
    "$TEMPLATE" > "$OUTPUT"

plutil -lint "$OUTPUT" > /dev/null
echo "plist 생성:      $OUTPUT"

mkdir -p "$PROJECT_ROOT/logs" "$PROJECT_ROOT/data"

# 기존 로드 해제 후 재로드
if launchctl list | grep -q com.ht.exercise-counter; then
    launchctl unload "$TARGET" 2>/dev/null || true
fi

cp "$OUTPUT" "$TARGET"
launchctl load "$TARGET"

echo "launchd 등록 완료: $TARGET"
echo "다음 실행 시각: 매일 23:00 KST"
echo ""
echo "확인: launchctl list | grep com.ht.exercise-counter"
echo "해제: launchctl unload $TARGET"

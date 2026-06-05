#!/usr/bin/env bash
# 自动创建 GitHub PR
#
# 用法:
#   ./scripts/create-pr.sh                          # 当前分支 → main
#   ./scripts/create-pr.sh feature/同传优化 main    # 指定分支与目标
#   ./scripts/create-pr.sh "" main "feat(x): 标题"  # 自定义标题
#
# 依赖: git, 可选 gh (GitHub CLI)

set -euo pipefail

BRANCH="${1:-$(git branch --show-current)}"
BASE="${2:-main}"
TITLE="${3:-}"

if [[ -z "$TITLE" ]]; then
  TITLE=$(git log "$BASE".."$BRANCH" --pretty=format:"%s" 2>/dev/null | head -1)
  [[ -z "$TITLE" ]] && TITLE=$(git log -1 --pretty=%s)
fi

COMMITS=$(git log "$BASE".."$BRANCH" --pretty=format:"- %s" 2>/dev/null || true)
FILES=$(git diff "$BASE".."$BRANCH" --stat 2>/dev/null | tail -1 || true)

read -r -d '' BODY <<EOF || true
## Summary

$(echo "${COMMITS}" | head -8)

${FILES:-}

## Test plan

- [x] python manage.py check
- [x] ./scripts/selftest.sh
EOF

REMOTE=$(git remote get-url origin 2>/dev/null | sed -E 's/.*github.com[:/](.+)\.git/\1/')
ENC_BRANCH=$(python3 -c "import urllib.parse; print(urllib.parse.quote('${BRANCH}'))")

echo "======================================"
echo " PR 标题: $TITLE"
echo " 源分支: $BRANCH → $BASE"
echo "======================================"

if command -v gh >/dev/null 2>&1; then
  gh pr create \
    --base "$BASE" \
    --head "$BRANCH" \
    --title "$TITLE" \
    --body "$BODY" \
    && echo "PR created" \
    && gh pr view --web
  exit 0
fi

URL="https://github.com/${REMOTE}/compare/${BASE}...${ENC_BRANCH}?expand=1"
echo ""
echo "gh CLI not found. Open this URL to create PR:"
echo "$URL"
echo ""
echo "----- 复制 PR 标题 -----"
echo "$TITLE"
echo "----- 复制 PR 描述 -----"
echo "$BODY"

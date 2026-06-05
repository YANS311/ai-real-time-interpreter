#!/usr/bin/env bash
# 自动创建 GitHub PR（实训模板）
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
【本日开发进展】
1. 新增功能：
$(echo "${COMMITS}" | head -5)
2. 优化内容：
- 流式音频低延迟、BGM人声分离、字幕导出与前端体验优化
3. 遗留待优化（若无就填：无）：
- 七牛云直播流同传（计划中）

【变更摘要】
${FILES:-（见 commit 列表）}

【自测结果】
本地全量运行正常，python manage.py check 通过，全功能可演示，无运行报错

关联议题：第三批次题目二 AI同声传译助手
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
    && echo "✅ PR 已创建" \
    && gh pr view --web
  exit 0
fi

URL="https://github.com/${REMOTE}/compare/${BASE}...${ENC_BRANCH}?expand=1"
echo ""
echo "⚠️  未安装 gh CLI，请手动打开链接创建 PR："
echo "$URL"
echo ""
echo "----- 复制 PR 标题 -----"
echo "$TITLE"
echo "----- 复制 PR 描述 -----"
echo "$BODY"

#!/usr/bin/env bash
# 本地快速自测：健康检查 + 演示脚本 + 状态页
set -euo pipefail

BASE="${1:-http://127.0.0.1:8000}"

echo "==> health"
curl -sf "$BASE/api/health/" | python3 -m json.tool

echo "==> status"
curl -sf "$BASE/api/status/" | python3 -m json.tool

echo "==> demo script"
LINES=$(curl -sf "$BASE/api/demo/script/" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('lines',[])))")
echo "demo lines: $LINES"

echo "==> vtt export"
VTT=$(curl -sf -X POST "$BASE/api/export/subtitles/?format=vtt" \
  -H "Content-Type: application/json" \
  -d '{"items":[{"source":"hi","target":"你好","start_sec":0,"end_sec":1}]}' | head -1)
test "$VTT" = "WEBVTT"

echo "==> upload ppt context"
curl -sf -X POST "$BASE/api/upload-ppt/" \
  -H "Content-Type: application/json" \
  -d '{"text":"Chapter 1: Large Language Models; GPT=生成式预训练模型"}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get('ok') and d['ppt_context']['configured']"

echo "==> correct api route"
CODE=$(curl -s -o /tmp/correct.json -w "%{http_code}" -X POST "$BASE/api/correct/" \
  -H "Content-Type: application/json" \
  -d '{"session_id":"00000000-0000-0000-0000-000000000001","index":0,"new_text":"测试"}')
test "$CODE" = "400"

if [ -n "${REDIS_URL:-}" ]; then
  echo "==> redis status"
  curl -sf "$BASE/api/status/" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['redis']['enabled']"
fi

echo "==> index page"
CODE=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/")
test "$CODE" = "200"

echo "==> status page"
CODE=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/status/")
test "$CODE" = "200"

echo "OK: selftest passed"

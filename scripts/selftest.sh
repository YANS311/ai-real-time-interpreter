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

echo "==> index page"
CODE=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/")
test "$CODE" = "200"

echo "==> status page"
CODE=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/status/")
test "$CODE" = "200"

echo "OK: selftest passed"

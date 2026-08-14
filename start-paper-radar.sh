#!/usr/bin/env bash
set -e

cd /home/seojin/widget/paper-search

URL="http://127.0.0.1:8765"

uv run paper-radar dashboard &
APP_PID=$!

# 서버가 뜰 때까지 최대 약 30초 대기
for i in {1..60}; do
  if command -v curl >/dev/null 2>&1; then
    if curl -fsS "$URL" >/dev/null 2>&1; then
      xdg-open "$URL" >/dev/null 2>&1 &
      break
    fi
  else
    if timeout 1 bash -c "cat < /dev/null > /dev/tcp/127.0.0.1/8765" 2>/dev/null; then
      xdg-open "$URL" >/dev/null 2>&1 &
      break
    fi
  fi
  sleep 0.5
done

wait "$APP_PID"

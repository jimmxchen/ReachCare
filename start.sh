#!/usr/bin/env bash
# Starts the Reach app and the iMessage bridge together. Ctrl+C stops both.
cd "$(dirname "$0")"
python3 server.py &
APP=$!
trap 'kill $APP $BOT 2>/dev/null' EXIT INT TERM
sleep 2
( cd bot && [ -d node_modules ] || npm install --no-audit --no-fund )
( cd bot && npm start ) &
BOT=$!
wait

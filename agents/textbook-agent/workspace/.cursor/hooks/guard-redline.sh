#!/bin/bash
# Cursor runs this as a shell command. Python decides; always print JSON.
dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ -f "$dir/guard-redline.py" ] && python3 "$dir/guard-redline.py"; then
  exit 0
fi
printf '%s\n' '{"permission":"deny","user_message":"权限钩子失败，已按失败关闭处理"}'

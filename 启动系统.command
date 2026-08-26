#!/bin/zsh
set -e

PROJECT_DIR="${0:A:h}"
cd "$PROJECT_DIR"

if [[ ! -x ".venv/bin/python" ]]; then
  echo "首次启动：正在创建本地运行环境……"
  python3 -m venv .venv
fi

PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"

if ! "$PYTHON_BIN" -c "import fastapi,uvicorn,httpx,openpyxl" >/dev/null 2>&1; then
  echo "首次启动：正在安装项目依赖，请稍候……"
  "$PYTHON_BIN" -m pip install -r requirements.txt
fi

SERVER_PORT="${PORT:-8000}"
SYSTEM_URL="http://127.0.0.1:${SERVER_PORT}"

echo "正在启动 TRM 科技资源管理系统……"
"$PYTHON_BIN" -m uvicorn app.runtime:app --host 127.0.0.1 --port "$SERVER_PORT" &
SERVER_PID=$!

cleanup() {
  if kill -0 "$SERVER_PID" >/dev/null 2>&1; then
    kill "$SERVER_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

for attempt in {1..80}; do
  if curl --silent --fail "$SYSTEM_URL/api/health" >/dev/null 2>&1; then
    echo "系统已启动：$SYSTEM_URL"
    if ! open "$SYSTEM_URL"; then
      echo "未能自动打开浏览器，请手动访问：$SYSTEM_URL"
    fi
    echo "请保留此窗口运行；关闭窗口或按 Control+C 可停止系统。"
    wait "$SERVER_PID"
    exit 0
  fi
  if ! kill -0 "$SERVER_PID" >/dev/null 2>&1; then
    wait "$SERVER_PID"
    exit 1
  fi
  sleep 0.25
done

echo "系统启动超时，请查看上方错误信息。"
exit 1

#!/usr/bin/env bash
# Wrapper script that loads secrets from .env and runs the live MACD bot
# in loop mode (one iteration every 15 minutes). Launched by launchd.

set -euo pipefail

# Resolve the absolute path to the repository root regardless of where the
# script is executed from.
SCRIPT_DIR="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
REPO_ROOT="$( cd -- "${SCRIPT_DIR}/../.." &> /dev/null && pwd )"

ENV_FILE="${SCRIPT_DIR}/.env"
if [[ ! -f "${ENV_FILE}" ]]; then
    echo "ERROR: ${ENV_FILE} not found." >&2
    echo "Copy .env.example to .env and fill in your TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID." >&2
    exit 1
fi

# Export every KEY=VALUE line from .env into the environment
set -o allexport
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +o allexport

cd "${REPO_ROOT}"

# Pick the python interpreter: prefer /opt/homebrew/bin/python3 (Apple Silicon
# Homebrew), fall back to /usr/local/bin/python3 (Intel Homebrew), then to the
# system python3 shipped with macOS / Xcode CLT.
if [[ -x /opt/homebrew/bin/python3 ]]; then
    PYBIN=/opt/homebrew/bin/python3
elif [[ -x /usr/local/bin/python3 ]]; then
    PYBIN=/usr/local/bin/python3
else
    PYBIN=/usr/bin/python3
fi

# caffeinate prevents macOS from putting the process to sleep when the display
# goes dark (but the machine still sleeps normally when the lid is closed).
# The "-i" flag keeps the system from idle-sleeping while this process runs.
exec /usr/bin/caffeinate -i "${PYBIN}" bot/live_macd_bot.py --loop 900

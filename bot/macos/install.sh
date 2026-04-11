#!/usr/bin/env bash
# One-shot installer: registers the bot as a launchd LaunchAgent so it
# runs 24/7 on your Mac and restarts automatically on reboot.

set -euo pipefail

SCRIPT_DIR="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
REPO_ROOT="$( cd -- "${SCRIPT_DIR}/../.." &> /dev/null && pwd )"

LABEL="com.btc-tournament.bot"
PLIST_SRC="${SCRIPT_DIR}/${LABEL}.plist"
PLIST_DST="${HOME}/Library/LaunchAgents/${LABEL}.plist"

# --- Sanity checks ---------------------------------------------------------

if [[ ! -f "${SCRIPT_DIR}/.env" ]]; then
    echo "ERROR: ${SCRIPT_DIR}/.env not found."
    echo
    echo "Create it from the template:"
    echo "    cp ${SCRIPT_DIR}/.env.example ${SCRIPT_DIR}/.env"
    echo "    \$EDITOR ${SCRIPT_DIR}/.env"
    echo
    echo "Fill in TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID, then re-run this script."
    exit 1
fi

# Make sure the wrapper is executable
chmod +x "${SCRIPT_DIR}/run_bot.sh"

# --- Self-test the bot once to confirm secrets + network work -------------

echo "[1/4] Testing the bot..."
cd "${REPO_ROOT}"
set -o allexport
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/.env"
set +o allexport

PYBIN="python3"
if [[ -x /opt/homebrew/bin/python3 ]]; then
    PYBIN=/opt/homebrew/bin/python3
elif [[ -x /usr/local/bin/python3 ]]; then
    PYBIN=/usr/local/bin/python3
fi

"${PYBIN}" bot/live_macd_bot.py --test
echo "    test message sent — check your Telegram."
echo

# --- Render the plist with the real repo path ----------------------------

echo "[2/4] Rendering LaunchAgent plist with REPO_ROOT=${REPO_ROOT}..."
mkdir -p "${HOME}/Library/LaunchAgents"
sed "s|__REPO_ROOT__|${REPO_ROOT}|g" "${PLIST_SRC}" > "${PLIST_DST}"

# --- Unload any previous version, then load the new one ------------------

echo "[3/4] (Re)loading the launchd agent..."
if launchctl list | grep -q "${LABEL}"; then
    launchctl unload "${PLIST_DST}" 2>/dev/null || true
fi
launchctl load -w "${PLIST_DST}"

# --- Verify ---------------------------------------------------------------

echo "[4/4] Verifying..."
sleep 2
if launchctl list | grep -q "${LABEL}"; then
    echo
    echo "SUCCESS — the bot is running in the background."
    echo
    echo "Useful commands:"
    echo "    tail -f ${REPO_ROOT}/bot/bot.log            # stream logs"
    echo "    launchctl list | grep ${LABEL}              # check status"
    echo "    launchctl unload ${PLIST_DST}               # stop the bot"
    echo "    launchctl load   ${PLIST_DST}               # start the bot"
    echo
    echo "The bot will auto-start every time you log in to your Mac."
else
    echo "ERROR: launchd did not register the agent. Check the logs:"
    echo "    cat ${REPO_ROOT}/bot/bot.log"
    exit 1
fi

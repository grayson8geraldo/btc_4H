#!/usr/bin/env bash
# One-shot installer: registers the bot as a launchd LaunchAgent so it
# runs 24/7 on your Mac and restarts automatically on reboot.

set -euo pipefail

SCRIPT_DIR="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
REPO_ROOT="$( cd -- "${SCRIPT_DIR}/../.." &> /dev/null && pwd )"

LABEL="com.btc-tournament.bot"
PLIST_SRC="${SCRIPT_DIR}/${LABEL}.plist"
PLIST_DST="${HOME}/Library/LaunchAgents/${LABEL}.plist"
DOMAIN_TARGET="gui/$(id -u)"

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

chmod +x "${SCRIPT_DIR}/run_bot.sh"

# Pick a python interpreter once and reuse it for test and install.
if [[ -x /opt/homebrew/bin/python3 ]]; then
    PYBIN=/opt/homebrew/bin/python3
elif [[ -x /usr/local/bin/python3 ]]; then
    PYBIN=/usr/local/bin/python3
elif command -v python3 >/dev/null 2>&1; then
    PYBIN="$(command -v python3)"
else
    echo "ERROR: python3 not found. Install it with:  brew install python@3.11"
    exit 1
fi

echo "Using python: ${PYBIN} ($(${PYBIN} --version 2>&1))"
echo "Repo root:    ${REPO_ROOT}"
echo "Agent label:  ${LABEL}"
echo

# --- [1/4] Self-test the bot to confirm secrets + network work ------------

echo "[1/4] Testing Telegram credentials..."
cd "${REPO_ROOT}"
set -o allexport
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/.env"
set +o allexport

# Strip trailing whitespace / newlines that may have been pasted in.
TELEGRAM_BOT_TOKEN="$(printf '%s' "${TELEGRAM_BOT_TOKEN:-}" | tr -d '[:space:]')"
TELEGRAM_CHAT_ID="$(printf '%s' "${TELEGRAM_CHAT_ID:-}" | tr -d '[:space:]')"
export TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID

set +e
"${PYBIN}" bot/live_macd_bot.py --test
TEST_RC=$?
set -e

if [[ ${TEST_RC} -ne 0 ]]; then
    echo
    echo "ERROR: Telegram test failed (exit ${TEST_RC})."
    echo
    echo "Common causes:"
    echo "  * TELEGRAM_BOT_TOKEN is wrong or revoked -> get a fresh one from @BotFather"
    echo "  * TELEGRAM_CHAT_ID is wrong -> open @userinfobot, copy the 'Id' field"
    echo "  * You never sent /start to your bot -> open the bot in Telegram and press Start"
    echo "  * Private group chat? chat_id must start with '-' and be numeric"
    echo
    echo "Fix ${SCRIPT_DIR}/.env and re-run this script."
    exit 1
fi
echo "      Telegram test passed."
echo

# --- [2/4] Render the plist with the real repo path and validate it ------

echo "[2/4] Rendering LaunchAgent plist..."
mkdir -p "${HOME}/Library/LaunchAgents"
# Use | as the sed delimiter to avoid clashes with path slashes.
sed "s|__REPO_ROOT__|${REPO_ROOT}|g" "${PLIST_SRC}" > "${PLIST_DST}"

if ! /usr/bin/plutil -lint "${PLIST_DST}"; then
    echo "ERROR: rendered plist failed plutil validation. Contents:"
    cat "${PLIST_DST}"
    exit 1
fi
echo "      Plist validated: ${PLIST_DST}"
echo

# --- [3/4] (Re)load the launchd agent -------------------------------------

echo "[3/4] Loading the launchd agent..."

# If the agent is already loaded, remove it first so we don't leak stale copies.
if /bin/launchctl print "${DOMAIN_TARGET}/${LABEL}" &>/dev/null; then
    echo "      removing existing agent..."
    /bin/launchctl bootout "${DOMAIN_TARGET}/${LABEL}" &>/dev/null || true
elif /bin/launchctl list 2>/dev/null | grep -q "${LABEL}"; then
    echo "      removing legacy agent..."
    /bin/launchctl unload "${PLIST_DST}" &>/dev/null || true
fi

# Prefer modern bootstrap (macOS 10.10+). It gives readable errors on failure.
if /bin/launchctl bootstrap "${DOMAIN_TARGET}" "${PLIST_DST}"; then
    echo "      bootstrapped via launchctl bootstrap"
else
    echo "      bootstrap failed, falling back to legacy launchctl load..."
    /bin/launchctl load -w "${PLIST_DST}"
fi

# Enable so it survives reboots + logouts
/bin/launchctl enable "${DOMAIN_TARGET}/${LABEL}" 2>/dev/null || true

echo

# --- [4/4] Verify ---------------------------------------------------------

echo "[4/4] Verifying..."
sleep 3

STATUS_OK=0
if /bin/launchctl print "${DOMAIN_TARGET}/${LABEL}" &>/dev/null; then
    STATUS_OK=1
elif /bin/launchctl list 2>/dev/null | grep -q "${LABEL}"; then
    STATUS_OK=1
fi

if [[ ${STATUS_OK} -eq 1 ]]; then
    echo
    echo "SUCCESS - the bot is running in the background."
    echo
    echo "Useful commands:"
    echo "    tail -f ${REPO_ROOT}/bot/bot.log"
    echo "    launchctl print ${DOMAIN_TARGET}/${LABEL}"
    echo "    launchctl bootout ${DOMAIN_TARGET}/${LABEL}   # stop"
    echo "    launchctl bootstrap ${DOMAIN_TARGET} ${PLIST_DST}   # start"
    echo
    echo "The bot will auto-start every time you log in."
    exit 0
fi

# --- Failure path: dump diagnostic info so we can see what went wrong ----

echo
echo "ERROR: launchd did not register the agent."
echo
echo "Rendered plist (${PLIST_DST}):"
echo "---"
cat "${PLIST_DST}"
echo "---"
echo
echo "launchctl list | grep btc-tournament:"
/bin/launchctl list 2>/dev/null | grep btc-tournament || echo "  (no matches)"
echo
echo "Last 50 lines of ${REPO_ROOT}/bot/bot.log:"
if [[ -f "${REPO_ROOT}/bot/bot.log" ]]; then
    tail -n 50 "${REPO_ROOT}/bot/bot.log" || true
else
    echo "  (log file not yet created)"
fi
echo
echo "Try running run_bot.sh manually to see the error directly:"
echo "    bash ${SCRIPT_DIR}/run_bot.sh"
exit 1

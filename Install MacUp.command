#!/bin/zsh
set -u

cd "$(dirname "$0")" || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
export PYTHONDONTWRITEBYTECODE=1

title() {
  printf "\n== %s ==\n" "$1"
}

fail() {
  printf "\nMacUp setup stopped: %s\n" "$1" >&2
  printf "Press Return to close this window."
  read -r _
  exit 1
}

have() {
  command -v "$1" >/dev/null 2>&1
}

have_xbar_app() {
  [ -d "/Applications/xbar.app" ] || [ -d "$HOME/Applications/xbar.app" ] || [ -d "./xbar (use what you need)/xbar.app" ]
}

install_formula_if_needed() {
  local formula="$1"
  local binary="$2"
  if have "$binary"; then
    printf "%s found: %s\n" "$binary" "$(command -v "$binary")"
    return 0
  fi
  have brew || return 1
  title "Installing $formula"
  brew install "$formula" || return 1
  have "$binary"
}

install_xbar_if_needed() {
  if have_xbar_app; then
    printf "xbar.app found.\n"
    return 0
  fi
  have brew || return 1
  title "Installing Xbar"
  brew install --cask xbar || return 1
  have_xbar_app
}

title "MacUp Setup"
printf "This installs MacUp prerequisites and opens the local setup manager.\n"
printf "You will still choose your password, OneDrive account, and backup folders in the browser.\n"

[ "$(uname -s)" = "Darwin" ] || fail "MacUp setup currently supports macOS only."
have python3 || fail "python3 was not found. Install Python 3, then run this again."
chmod +x ./macup 2>/dev/null || true

if ! have brew; then
  printf "\nHomebrew was not found. MacUp can still run if restic, rclone, and Xbar are already installed.\n"
  printf "For automatic dependency installation, install Homebrew from https://brew.sh and run this again.\n"
fi

install_formula_if_needed restic restic || fail "restic is missing and could not be installed automatically."
install_formula_if_needed rclone rclone || fail "rclone is missing and could not be installed automatically."
install_xbar_if_needed || fail "xbar.app is missing and could not be installed automatically."

title "Local Checks"
./macup doctor || true

title "Opening MacUp Manager"
printf "Complete onboarding in the browser. When you are done, click Stop Manager.\n"
./macup manager

title "Finishing Setup"
if python3 - <<'PY'
from macup_tool import keychain, rclone_config
from macup_tool.config import load_config

cfg = load_config()
ready = bool(cfg.get("sources")) and bool(cfg.get("initialized"))
ready = ready and bool(cfg.get("rclone_configured") or rclone_config.remote_exists(cfg))
ready = ready and keychain.find_password(keychain.RESTIC_SERVICE, keychain.RESTIC_ACCOUNT) is not None
raise SystemExit(0 if ready else 1)
PY
then
  ./macup install || fail "MacUp finished onboarding, but scheduler/Xbar installation failed."
  printf "\nMacUp is installed. Xbar should show the status circle in the menu bar.\n"
else
  printf "MacUp onboarding is not complete yet. Run this installer again or run ./macup manager to continue.\n"
fi

printf "\nPress Return to close this window."
read -r _

#!/usr/bin/env bash
# Install the Linux sandbox without launching Astrion, its CLI, or its backend.
set -euo pipefail

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
installer="${script_directory}/../modules/linux_sandbox/setup.py"
sandbox_user="${SUDO_USER:-}"
dry_run=false
install_dependencies=true
status_only=false

usage() {
  cat <<'HELP'
Astrion Linux sandbox installer (Ubuntu 24.04)

  sudo bash scripts/setup-linux-sandbox.sh
  sudo bash scripts/setup-linux-sandbox.sh --user astrion
  bash scripts/setup-linux-sandbox.sh --dry-run
  bash scripts/setup-linux-sandbox.sh --status

Options:
  --user USER                 Ordinary OS account that runs Astrion.
                              Defaults to SUDO_USER, or the current non-root user.
  --dry-run                   Show installation targets; do not change the system.
  --no-install-dependencies   Require existing dependencies; do not run apt.
  --status                    Check helper as the current ordinary user.
  -h, --help                  Show this help.

Installation adds system packages, an immutable helper, AppArmor profiles and
an administrator service. Upgrades retain a backup of the previous helper.
Astrion itself must continue to run as an ordinary OS user, without sudo.
HELP
}

while (($#)); do
  case "$1" in
    --user)
      if (($# < 2)); then echo '--user requires an OS account' >&2; exit 2; fi
      sandbox_user="$2"
      shift 2
      ;;
    --dry-run) dry_run=true; shift ;;
    --no-install-dependencies) install_dependencies=false; shift ;;
    --status) status_only=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ ! -f "$installer" ]]; then
  echo "Installer source is missing: $installer" >&2
  exit 1
fi
if [[ ! -x /usr/bin/python3 ]]; then
  echo 'Python 3 is required. On Ubuntu: sudo apt-get install python3' >&2
  exit 1
fi
if $status_only; then
  exec /usr/bin/python3 -I "$installer" status
fi
if [[ -z "$sandbox_user" && "$EUID" -ne 0 ]]; then
  sandbox_user="$(id -un)"
fi
if [[ -z "$sandbox_user" ]]; then
  echo 'When installing as root, specify the ordinary Astrion account with --user USER.' >&2
  exit 2
fi
arguments=(install --user "$sandbox_user")
if $dry_run; then
  arguments+=(--dry-run)
elif [[ "$EUID" -ne 0 ]]; then
  echo 'Installation requires administrator permission. Re-run this script through sudo.' >&2
  exit 1
fi
if $install_dependencies; then
  arguments+=(--install-dependencies)
fi
exec /usr/bin/python3 -I "$installer" "${arguments[@]}"

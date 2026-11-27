#!/usr/bin/env bash
# Install the independently built Linux sandbox without starting the backend.
set -euo pipefail

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
source_root="$(dirname -- "$script_directory")"
program="$source_root/astrion-linux-sandbox"
[[ -x "$program" ]] || program="$source_root/backend-rust/target/release/astrion-linux-sandbox"

case "${1:-}" in
  -h|--help)
    cat <<'HELP'
Astrion Linux sandbox installer (Ubuntu 24.04)

  sudo bash scripts/setup-linux-sandbox.sh
  sudo bash scripts/setup-linux-sandbox.sh --user astrion
  bash scripts/setup-linux-sandbox.sh --dry-run
  bash scripts/setup-linux-sandbox.sh --status

Options:
  --user USER                 Ordinary OS account running Astrion.
                              Defaults to SUDO_USER or the current non-root user.
  --dry-run                   Show installation targets without modifying the system.
  --no-install-dependencies   Require existing system dependencies.
  --status                    Check the installed helper as the ordinary user.
  -h, --help                  Show this help.
HELP
    exit 0
    ;;
  --status)
    if (($# != 1)); then echo '--status takes no additional options' >&2; exit 2; fi
    installed=/usr/local/libexec/astrion-sandbox/astrion-linux-sandbox
    [[ ! -x "$installed" ]] || program="$installed"
    if [[ -x "$program" ]]; then exec "$program" status; fi
    ;;
esac

if [[ ! -x "$program" ]]; then
  echo 'The Linux sandbox binary is missing. Build it as an ordinary user:' >&2
  echo 'cargo build --locked --release --manifest-path backend-rust/Cargo.toml -p astrion-linux-sandbox' >&2
  exit 1
fi
exec "$program" install "$@"

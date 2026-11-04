#!/usr/bin/env bash
# Run OPP simulator commands inside the toolchain container (see wsl/Dockerfile).
#
# The repository is mounted at the same absolute path inside the container and
# the host /tmp is shared, so every path the runners print (results/, work_dir=)
# is valid in your normal WSL shell too. Files are created as your own user.
#
# Usage (from anywhere inside the repository):
#   wsl/opp.sh build [waf configure options]   configure + build ns-3
#   wsl/opp.sh smoke                           run the 2-node sanity simulation
#   wsl/opp.sh shell                           interactive shell in the container
#   wsl/opp.sh <command> [args...]             run any command, e.g.
#       wsl/opp.sh python3 experiment_reproduction/figure6/run_figure6.py --jobs 8
#
# Environment overrides:
#   OPP_ENGINE=docker|podman   container engine (default: whichever works)
#   OPP_IMAGE=name:tag         image name (default: opp-ns3:18.04)
#   OPP_REBUILD=1              rebuild the image before running
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"
SIM_DIR="$REPO_ROOT/NS3/OPP/simulation"
IMAGE="${OPP_IMAGE:-opp-ns3:18.04}"

die() { printf 'error: %s\n' "$*" >&2; exit 1; }

pick_engine() {
  if [[ -n "${OPP_ENGINE:-}" ]]; then
    command -v "$OPP_ENGINE" >/dev/null 2>&1 || die "OPP_ENGINE=$OPP_ENGINE not found"
    printf '%s\n' "$OPP_ENGINE"; return
  fi
  if command -v docker >/dev/null 2>&1; then
    if docker info >/dev/null 2>&1; then printf 'docker\n'; return; fi
    if ! command -v podman >/dev/null 2>&1; then
      die "docker is installed but not usable by $(id -un).
  Start it:            sudo systemctl enable --now docker
  Allow your user:     sudo usermod -aG docker $(id -un)   (then open a new WSL terminal)
  Or install podman:   sudo apt install -y podman"
    fi
  fi
  if command -v podman >/dev/null 2>&1; then printf 'podman\n'; return; fi
  die "no container engine found. Install one:
  sudo apt install -y podman            (simplest, no daemon)
  or: sudo apt install -y docker.io && sudo usermod -aG docker $(id -un)"
}

case "$REPO_ROOT" in
  /mnt/[a-zA-Z]/*)
    printf 'warning: repository is on the Windows filesystem (%s).\n' "$REPO_ROOT" >&2
    printf '         Builds and simulations will be very slow. Copy it into WSL with:\n' >&2
    printf '         python3 wsl/prepare_wsl.py --copy-to ~/OmniPath-Ping\n\n' >&2 ;;
esac

ensure_image() {
  if [[ "${OPP_REBUILD:-0}" == "1" ]] || ! "$ENGINE" image inspect "$IMAGE" >/dev/null 2>&1; then
    printf 'Building container image %s (one time, needs internet)...\n' "$IMAGE" >&2
    "$ENGINE" build -t "$IMAGE" "$SCRIPT_DIR"
  fi
}

# run_in <workdir> <command...>
run_in() {
  local workdir="$1"; shift
  local -a args=(run --rm --init -i
    -v "$REPO_ROOT:$REPO_ROOT"
    -v /tmp:/tmp
    -w "$workdir"
    -e HOME=/tmp)
  [[ -t 0 && -t 1 ]] && args+=(-t)
  if [[ "$ENGINE" == "podman" ]]; then
    args+=(--userns=keep-id)
  else
    args+=(--user "$(id -u):$(id -g)")
  fi
  "$ENGINE" "${args[@]}" "$IMAGE" "$@"
}

# Work in the caller's directory when it is inside the repository, so relative
# paths typed by the user behave exactly as they would natively.
HERE="$(pwd -P)"
case "$HERE/" in
  "$REPO_ROOT"/*) ;;
  *) HERE="$REPO_ROOT/NS3/OPP" ;;
esac

cmd="${1:-shell}"
if [[ "$cmd" == "-h" || "$cmd" == "--help" || "$cmd" == "help" ]]; then
  sed -n '2,19p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit 0
fi

ENGINE="$(pick_engine)"
ensure_image

case "$cmd" in
  build)
    shift
    run_in "$SIM_DIR" bash -c 'set -e; python2 ./waf configure "$@"; python2 ./waf build' opp-build "$@"
    ;;
  smoke)
    run_in "$SIM_DIR" bash -c 'set -e
      python2 ./waf --run "scratch/third mix/minimal_1to1_config.txt"
      echo; echo "== mix/minimal_1to1_fct.txt"; cat mix/minimal_1to1_fct.txt'
    ;;
  shell)
    run_in "$HERE" bash
    ;;
  *)
    run_in "$HERE" "$@"
    ;;
esac

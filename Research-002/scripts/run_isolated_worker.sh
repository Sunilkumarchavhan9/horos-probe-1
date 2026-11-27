#!/usr/bin/env bash
# ==============================================================================
# HOROS PROBE-1 — Isolated Inference Worker Launcher Script
# Launches worker inside OCI container or Linux namespace with strict isolation:
# - UID/GID 10001:10001
# - Network none
# - Read-only root
# - Cap-drop ALL
# - No private vault mounted
# ==============================================================================
set -euo pipefail

IMAGE_NAME="${HOROS_WORKER_IMAGE:-horos-probe1-worker:stage37c2}"
CHECKPOINT_PATH="${1:-}"

if [[ -z "${CHECKPOINT_PATH}" ]]; then
  echo "Usage: $0 <path-to-candidate-checkpoint.pt>" >&2
  exit 1
fi

if [[ ! -f "${CHECKPOINT_PATH}" ]]; then
  echo "Error: Candidate checkpoint not found at: ${CHECKPOINT_PATH}" >&2
  exit 2
fi

CHECKPOINT_ABS="$(cd "$(dirname "${CHECKPOINT_PATH}")" && pwd)/$(basename "${CHECKPOINT_PATH}")"

# Determine runtime: Docker, Podman, or unshare
if command -v docker &>/dev/null; then
  RUNTIME="docker"
elif command -v podman &>/dev/null; then
  RUNTIME="podman"
elif command -v unshare &>/dev/null; then
  RUNTIME="unshare"
else
  echo "Error: Neither docker, podman, nor unshare is available on this host." >&2
  exit 3
fi

echo "[HOROS ISOLATION] Launching inference worker via ${RUNTIME}..." >&2

if [[ "${RUNTIME}" == "docker" || "${RUNTIME}" == "podman" ]]; then
  exec "${RUNTIME}" run --rm -i \
    --network none \
    --read-only \
    --user 10001:10001 \
    --cap-drop ALL \
    --security-opt no-new-privileges:true \
    --tmpfs /tmp:rw,noexec,nosuid,size=64m \
    --memory 2g \
    --cpus 2 \
    --pids-limit 128 \
    -v "${CHECKPOINT_ABS}:/model/checkpoint.pt:ro" \
    "${IMAGE_NAME}" \
    --worker
elif [[ "${RUNTIME}" == "unshare" ]]; then
  exec unshare -n -r -m -u -p -f --mount-proc \
    python3 -u -m world_model_vnext.worker_isolation --worker
fi

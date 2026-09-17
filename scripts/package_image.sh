#!/usr/bin/env bash
# Build a portable Asymptote image and package it for a machine that cannot
# build one: an air-gapped network, a customer site, an appliance.
#
#   ./scripts/package_image.sh                       # default image
#   ./scripts/package_image.sh --offline-bundle      # + reranker/Whisper/OCR models
#   ./scripts/package_image.sh --tag 1.4.0 --out /media/transfer
#
# Produces, in the output directory:
#   asymptote-<tag>.tar.gz        the image (docker load < this)
#   asymptote-<tag>.tar.gz.sha256 checksum to verify after the transfer
#   docker-compose.onprem.yml     how to run it
#   .env.onprem.example           what to configure
#   ONPREM.md, AIRGAP.md          the deployment guides
#
# Copy that directory across and follow ONPREM.md. Nothing else is needed on
# the target host beyond Docker itself.
set -euo pipefail

cd "$(dirname "$0")/.."

TAG="local"
OUT="dist-image"
OFFLINE_BUNDLE=0
WITH_DOCLING=0
WHISPER_MODEL="base"
PLATFORM=""

usage() {
    sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --tag) TAG="$2"; shift 2 ;;
        --out) OUT="$2"; shift 2 ;;
        --offline-bundle) OFFLINE_BUNDLE=1; shift ;;
        --with-docling) WITH_DOCLING=1; shift ;;
        --whisper-model) WHISPER_MODEL="$2"; shift 2 ;;
        # Build for the target's architecture when it differs from this
        # machine's (an arm64 laptop packaging for an amd64 server).
        --platform) PLATFORM="$2"; shift 2 ;;
        -h|--help) usage 0 ;;
        *) echo "unknown option: $1" >&2; usage 1 ;;
    esac
done

IMAGE="asymptote:${TAG}"
VCS_REF="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"

echo "==> Building ${IMAGE} (offline_bundle=${OFFLINE_BUNDLE}, docling=${WITH_DOCLING})"
build_args=(
    --build-arg "OFFLINE_BUNDLE=${OFFLINE_BUNDLE}"
    --build-arg "WITH_DOCLING=${WITH_DOCLING}"
    --build-arg "WHISPER_MODEL=${WHISPER_MODEL}"
    --build-arg "APP_VERSION=${TAG}"
    --build-arg "VCS_REF=${VCS_REF}"
)
[ -n "$PLATFORM" ] && build_args+=(--platform "$PLATFORM")

docker build "${build_args[@]}" -t "$IMAGE" .

mkdir -p "$OUT"
ARCHIVE="${OUT}/asymptote-${TAG}.tar.gz"

echo "==> Saving ${IMAGE} to ${ARCHIVE}"
docker save "$IMAGE" | gzip > "$ARCHIVE"

echo "==> Checksumming"
# The checksum is what the receiving side verifies before loading; keep it
# beside the archive with a bare filename so `sha256sum -c` works there.
( cd "$OUT" && sha256sum "asymptote-${TAG}.tar.gz" > "asymptote-${TAG}.tar.gz.sha256" )

echo "==> Copying deployment files"
cp docker-compose.onprem.yml .env.onprem.example "$OUT/"
cp docs/ONPREM.md docs/AIRGAP.md "$OUT/"

cat > "${OUT}/README.txt" <<EOF
Asymptote ${TAG} — offline install bundle
built $(date -u +%Y-%m-%dT%H:%M:%SZ) from ${VCS_REF}
offline model bundle: ${OFFLINE_BUNDLE}   docling OCR: ${WITH_DOCLING}

On the target machine:

  sha256sum -c asymptote-${TAG}.tar.gz.sha256
  docker load < asymptote-${TAG}.tar.gz
  cp .env.onprem.example .env      # then edit: model endpoint, AUTH_PASSWORD
  # set ASYMPTOTE_IMAGE=asymptote:${TAG} in .env
  docker compose -f docker-compose.onprem.yml up -d

Then open http://<host>:8473. Full guide: ONPREM.md (air gap: AIRGAP.md).
EOF

echo
echo "Bundle ready in ${OUT}:"
ls -lh "$OUT"

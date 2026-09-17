#!/bin/sh
# Container entrypoint.
#
# Everything here is about making ONE image portable: the same bytes an
# operator loads from a tarball, or pulls from a registry, have to work
# against whatever private PKI and endpoints the site runs. Anything baked at
# build time can't do that — the image would need rebuilding per site.
#
# Steps, all no-ops when nothing is mounted:
#   1. Trust the site's CA certificates (mount them at /certs/ca).
#   2. Make the Python HTTP clients trust them too — they pin certifi's own
#      bundle, so the system trust store alone is not enough.
#   3. Hand over to the CMD (uvicorn).
set -e

CA_DIR="${EXTRA_CA_CERTS_DIR:-/certs/ca}"

ca_files() {
    # Only real files: an empty mount, or one holding just a .gitkeep, must
    # leave the trust store untouched rather than emptying it.
    find "$CA_DIR" -maxdepth 1 -type f \( -name '*.crt' -o -name '*.pem' \) 2>/dev/null
}

if [ -d "$CA_DIR" ] && [ -n "$(ca_files)" ]; then
    echo "entrypoint: installing CA certificates from $CA_DIR"

    # 1. System trust store (curl, the healthcheck, anything linking OpenSSL).
    #    update-ca-certificates only reads .crt, so .pem files are copied
    #    under a .crt name rather than silently ignored.
    for f in $(ca_files); do
        cp "$f" "/usr/local/share/ca-certificates/$(basename "$f" | sed 's/\.pem$/.crt/')"
    done
    update-ca-certificates >/dev/null

    # 2. certifi's bundle, which httpx (and therefore the OpenAI SDK, the
    #    Anthropic SDK and every OpenAI-compatible endpoint call) uses
    #    instead of the system store. Rebuilt from a pristine copy on every
    #    start so restarting the container can't append the same CA twice,
    #    and so removing a CA from the mount actually removes it.
    CERTIFI_PEM="$(python -c 'import certifi; print(certifi.where())' 2>/dev/null || true)"
    if [ -n "$CERTIFI_PEM" ] && [ -f "$CERTIFI_PEM" ]; then
        [ -f "$CERTIFI_PEM.orig" ] || cp "$CERTIFI_PEM" "$CERTIFI_PEM.orig"
        cp "$CERTIFI_PEM.orig" "$CERTIFI_PEM"
        for f in $(ca_files); do
            printf '\n' >> "$CERTIFI_PEM"
            cat "$f" >> "$CERTIFI_PEM"
        done
    fi
fi

# The data directory is a bind mount or a volume, so it may arrive empty or
# owned by whoever created it on the host.
mkdir -p "${DATA_DIR:-/app/data}/documents" "${DATA_DIR:-/app/data}/indexes"

exec "$@"

#!/usr/bin/env bash
# Generate a self-signed TLS certificate for development / initial deployment.
# For production, replace with certs from your enterprise CA or Let's Encrypt.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SSL_DIR="${SCRIPT_DIR}/ssl"

mkdir -p "$SSL_DIR"

if [ -f "$SSL_DIR/server.crt" ] && [ -f "$SSL_DIR/server.key" ]; then
    echo "Certificates already exist in $SSL_DIR — skipping generation."
    echo "Delete them and re-run this script to regenerate."
    exit 0
fi

echo "Generating self-signed TLS certificate..."

# Write a minimal openssl config to avoid -subj path mangling on Windows/Git Bash
OPENSSL_CONF_TMP="$SSL_DIR/_openssl_tmp.cnf"
cat > "$OPENSSL_CONF_TMP" <<'SSLCONF'
[req]
default_bits       = 2048
prompt             = no
distinguished_name = dn
x509_extensions    = v3_ext

[dn]
C  = US
ST = Local
L  = Local
O  = Asymptote
OU = Enterprise
CN = localhost

[v3_ext]
subjectAltName = DNS:localhost,DNS:asymptote,IP:127.0.0.1
SSLCONF

openssl req -x509 -nodes -days 365 \
    -newkey rsa:2048 \
    -keyout "$SSL_DIR/server.key" \
    -out "$SSL_DIR/server.crt" \
    -config "$OPENSSL_CONF_TMP"

rm -f "$OPENSSL_CONF_TMP"

chmod 600 "$SSL_DIR/server.key"
chmod 644 "$SSL_DIR/server.crt"

echo "Done. Files written to:"
echo "  $SSL_DIR/server.crt"
echo "  $SSL_DIR/server.key"
echo ""
echo "For production, replace these with certificates from your enterprise CA."

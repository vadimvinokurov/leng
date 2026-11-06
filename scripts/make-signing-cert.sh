#!/usr/bin/env bash
# Generates the self-signed "Leng Developer" code-signing certificate once:
# a stable signing identity so TCC permissions survive app updates
# (ad-hoc linker signing produces a fresh identity per build).
#
# Outputs, gitignored under .leng-signing/:
#   leng-developer-key.pem          private key
#   leng-developer.crt              PEM certificate
#   leng-developer.p12              PKCS#12 for the CI secret
#   cert-password.txt               the P12 password (keep it safe)
set -euo pipefail

dir="$(cd "$(dirname "$0")/.." && pwd)/.leng-signing"
mkdir -p "$dir"

passfile="$dir/cert-password.txt"
if [ ! -f "$passfile" ]; then
  LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 24 > "$passfile"
fi
password="$(cat "$passfile")"

key="$dir/leng-developer-key.pem"
crt="$dir/leng-developer.crt"
p12="$dir/leng-developer.p12"
cn="Leng Developer"

if [ -f "$p12" ]; then
  echo "sig: $p12 exists; remove it first to regenerate"
  exit 0
fi

umask 077
# RSA 2048, 10 years, codeSigning extended key usage.
openssl req -newkey rsa:2048 -nodes -x509 \
  -subj "/CN=$cn/O=leng/C=RU" \
  -addext "basicConstraints=critical,CA:FALSE" \
  -addext "keyUsage=critical,digitalSignature" \
  -addext "extendedKeyUsage=codeSigning" \
  -keyout "$key" -out "$crt" -days 3650 >/dev/null 2>&1

openssl pkcs12 -export \
  -inkey "$key" -in "$crt" -name "$cn" \
  -passout "pass:$password" -out "$p12" >/dev/null 2>&1

echo "sig: key, cert and p12 written to $dir"
echo "sig: p12 password is in $passfile (never commit it)"
# Import into the login keychain as a codesigning identity.
security import "$p12" -k "$HOME/Library/Keychains/login.keychain-db" \
  -P "$password" -T /usr/bin/codesign
echo "sig: imported into the login keychain as \"$cn\""
echo "sig: codesign will use it via: codesign --sign \"$cn\" <bundle>"

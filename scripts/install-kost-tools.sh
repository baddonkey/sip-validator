#!/usr/bin/env bash
# Installs KOST-Tools (contains KOST-Val + JRE) into the Wine prefix of the current user.
set -euo pipefail

VERSION="${KOST_TOOLS_VERSION:-1.3.1.0}"
SHA256="${KOST_TOOLS_SHA256:-bb8e7c197b8a4da8442f5b8395c3df44213c171aa1863e656322f41a49c910ae}"
URL="${KOST_TOOLS_URL:-https://github.com/KOST-CECO/KOST-Val/releases/download/v${VERSION}/KOST-Tools_${VERSION}.msi}"

export WINEPREFIX="${WINEPREFIX:-$HOME/.wine}"
export WINEARCH="${WINEARCH:-win64}"
export WINEDEBUG="${WINEDEBUG:--all}"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "Downloading $URL"
curl -fsSL -o "$tmp/kost-tools.msi" "$URL"
echo "$SHA256  $tmp/kost-tools.msi" | sha256sum -c -

echo "Initialising Wine prefix $WINEPREFIX"
xvfb-run -a wineboot --init
wineserver -w
mkdir -p "$WINEPREFIX/drive_c/users/kost/AppData/Local/Temp"

echo "Installing KOST-Tools $VERSION"
if ! timeout --kill-after=10s 600s xvfb-run -a wine msiexec /i "$tmp/kost-tools.msi" /qn "/l*v" "$tmp/msi.log"; then
    tail -n 50 "$tmp/msi.log" 2>/dev/null || true
    exit 1
fi
wineserver -w

echo "KOST-Val CLI candidates:"
find "$WINEPREFIX/drive_c" -iname '*kost-val*' -a -iname '*cmd*' \( -iname '*.exe' -o -iname '*.jar' \) -print

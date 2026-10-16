#!/usr/bin/env bash
# Build the PS3 simulator image: XPAD Revolution's unmodified VSH plugin source + the lv2/VSH
# shim, as a static 32-bit big-endian PowerPC executable (same byte order and pointer width
# as the PS3 PPU). Run it with: qemu-ppc build/ps3sim  (see bitdo_ps3/simrun.py).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
xpad="${XPAD_SRC:-$here/../refs/XPAD-Revolution/src/ps3}"
out="$here/build"
mkdir -p "$out"
CC="${CC:-powerpc-linux-gnu-gcc}"
CFLAGS=(-D_GNU_SOURCE -O2 -g -static -pthread -fno-strict-aliasing -std=gnu11
        -I"$here/shim/include" -I"$here/shim" -include ps3shim.h
        -Wall -Wno-int-conversion -Wno-pointer-to-int-cast -Wno-int-to-pointer-cast
        -Wno-unused-variable -Wno-unused-function -Wno-unused-but-set-variable
        -Wno-incompatible-pointer-types -Wno-missing-braces)
"$CC" "${CFLAGS[@]}" -o "$out/ps3sim" "$xpad/main.c" "$here/shim/lv2_shim.c" "$here/shim/vsh_main.c"
sha256sum "$xpad/main.c" > "$out/plugin_source.sha256"
file "$out/ps3sim"

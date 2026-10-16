#!/usr/bin/env bash
# Build the PS3 simulator image: XPAD Revolution's VSH plugin source + the lv2/VSH shim, as a
# static 32-bit big-endian PowerPC executable (same byte order and pointer width as the PS3 PPU).
# Run it with: qemu-ppc build/ps3sim  (see bitdo_ps3/simrun.py).
#   build/ps3sim        the plugin source exactly as published
#   build/ps3sim_fixed  the same plus the freeze fix `8bitdo-ps3 ps3 start` applies in memory on the
#                       console: at 0x8a38 the game loader's libpad poll check always returns. In the
#                       source that check is `if (game_loader_last_pad_poll != 0 && ...) return;`
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
xpad="${XPAD_SRC:-$here/../refs/XPAD-Revolution/src/ps3}"
out="$here/build"
mkdir -p "$out"
CC="${CC:-powerpc-linux-gnu-gcc}"
CFLAGS=(-D_GNU_SOURCE -O2 -g -static -pthread -fno-strict-aliasing -std=gnu11
        -I"$here/shim/include" -I"$here/shim" -I"$xpad" -include ps3shim.h
        -Wall -Wno-int-conversion -Wno-pointer-to-int-cast -Wno-int-to-pointer-cast
        -Wno-unused-variable -Wno-unused-function -Wno-unused-but-set-variable
        -Wno-incompatible-pointer-types -Wno-missing-braces)
build() { "$CC" "${CFLAGS[@]}" -o "$1" "$2" "$here/shim/lv2_shim.c" "$here/shim/vsh_main.c"; }

build "$out/ps3sim" "$xpad/main.c"

tr -d '\r' < "$xpad/main.c" > "$out/main_lf.c"   # a Windows checkout may have CRLF endings
anchor='  if (game_loader_last_pad_poll != 0 &&'
if [ "$(grep -c -x -F "$anchor" "$out/main_lf.c")" != 1 ]; then
  echo "freeze-fix anchor not found exactly once in $xpad/main.c" >&2
  exit 1
fi
sed 's|^  if (game_loader_last_pad_poll != 0 &&$|  return; /* 8bitdo-ps3 freeze fix */ if (0 \&\&|' \
  "$out/main_lf.c" > "$out/main_freeze_fix.c"
grep -q -F 'return; /* 8bitdo-ps3 freeze fix */ if (0 &&' "$out/main_freeze_fix.c"
build "$out/ps3sim_fixed" "$out/main_freeze_fix.c"

sha256sum "$xpad/main.c" "$out/main_freeze_fix.c" > "$out/plugin_source.sha256"
file "$out/ps3sim" "$out/ps3sim_fixed"

#!/usr/bin/env bash
# One-time: the toolchain for the PS3 simulator, inside WSL Ubuntu (tested on 24.04).
#   wsl -d Ubuntu -- bash sim/setup_wsl.sh
set -euo pipefail
sudo apt-get update
sudo apt-get install -y gcc-powerpc-linux-gnu libc6-dev-powerpc-cross qemu-user file
powerpc-linux-gnu-gcc --version | head -1
qemu-ppc --version | head -1

# How it works

This page explains why the setup looks the way it does: why the plugin is started from a PC or from
an `autoexec.bat` script instead of `boot_plugins.txt`, and what the freeze fix changes. You don't
need any of it to use the tool.

- [The controller and its receiver](#the-controller-and-its-receiver)
- [XPAD Revolution](#xpad-revolution)
- [Why not boot_plugins.txt](#why-not-boot_pluginstxt)
- [The XMB freeze and the fix](#the-xmb-freeze-and-the-fix)
- [What `ps3 start` sends to webMAN](#what-ps3-start-sends-to-webman)
- [Starting without a PC (`ps3 autorun`)](#starting-without-a-pc-ps3-autorun)
- [Audit of the plugin files](#audit-of-the-plugin-files)
- [The simulated PS3](#the-simulated-ps3)

## The controller and its receiver

| | |
|---|---|
| Controller | 8BitDo Ultimate 2 Bluetooth |
| Receiver, no controller linked | USB `2DC8:3109`: only a vendor HID channel (usage page `0x8C`) used by 8BitDo's software |
| Receiver, controller linked | USB `2DC8:310B`: an Xbox 360 style (XInput) interface, interrupt IN `0x81`, OUT `0x01`, 32-byte packets |
| Receiver product ID / firmware | `0x6011`, firmware 1.03. These were read with the read-only query 8BitDo Ultimate Software V2 sends on connect (`81 04 00 01`, reply `02 04 …`) |

The controller has a 6-axis motion sensor, but 8BitDo lists motion as "for Switch only". In
XInput mode, over the receiver or the cable, no motion data is sent. XPAD Revolution also fills the
DualShock 3 motion fields with a fixed neutral value (`0x0200`) for every controller it supports.
That is why games see a still controller.

Windows has an XInput driver, so the receiver works on a PC out of the box. The PS3 has no driver for
XInput devices; it only drives its own controllers and standard HID pads. A homebrew plugin has to
add that driver. **Nothing on the receiver or the controller is modified.** They behave the same on
the PC and the PS3, which is why switching is just a matter of where you plug the receiver.

## XPAD Revolution

[XPAD Revolution](https://github.com/GabaRSk/XPAD-Revolution) (GabaRSk, based on OsirisX's
PS3xPAD) is a VSH plugin, meaning it runs inside the PS3's system software (the XMB). It:

1. registers an extra USB driver (`cellUsbdRegisterExtraLdd`) for the controller IDs it knows.
   `2DC8:310B` is built in, named "8BitDo Ultimate 2 Wireless (2.4GHz dongle)";
2. reads the XInput input report and converts it to a DualShock 3 style pad. Buttons are
   positional, so the bottom face button is Cross. The sticks get a DS3-like curve
   (`xpad_analog.txt`), and L2/R2 keep their full 0–255 range;
3. inserts that pad into the system (`cellPadLddDataInsert`), so the XMB and games see a normal
   controller on the next free port;
4. optionally loads `xpad_game.sprx` into a running game when you hold SELECT + L3 + R3. That
   in-game module handles rumble.

**Known issue: the in-game rumble module hangs the console on this HEN build.** On the test console
(4.92 CEX, PS3HEN 3.4.0, webMAN MOD 1.47.48n) the load itself succeeds — the log reads
`LOAD_PROC_MODULE returned OK` then `game module active` then `all hooks active` — and the console
freezes on that last line, with no further log output. So on this build, **don't hold SELECT + L3 +
R3 in a game**: rumble is not usable through XPAD's game module. The controller works fully
otherwise, and rumble works normally when the same controller is used on a PC (`8bitdo-ps3 pc-test`
pulses the motors through XInput). `8bitdo-ps3 ps3 log` now reports this hang explicitly.

It also sends live telemetry over UDP port 39001, which `8bitdo-ps3 ps3 live` displays.

## Why not boot_plugins.txt

The usual way to run a VSH plugin on HEN is a line in `/dev_hdd0/boot_plugins.txt`. On the test
console (4.92 CEX, PS3HEN 3.4.0, webMAN MOD 1.47.48n), XPAD loaded that way made **"Enable HEN"
hang**. The console had to be forced off, and recovery needed the R2 / USB-stick tricks described in
the README. Loading the same file later, from the XMB, through webMAN, works. So this tool never
adds XPAD to `boot_plugins.txt`, and it removes the line if it finds one.

An `autoexec.bat` webMAN script was also tried, and an early version of this tool failed with it:
webMAN 1.47.48 has no "load once" command (`/loadprx_ps3` returns HTTP 400), and the script was
expected to be unable to apply the fix. Both turned out to be solvable — see
[Starting without a PC](#starting-without-a-pc-ps3-autorun) — so that is now the recommended,
PC-free way to start. `ps3 install` still removes the block the early version wrote there (its
markers were `# >>> 8bitdo-ps3`, distinct from the current block).

## The XMB freeze and the fix

Loaded at runtime, the unmodified plugin froze the XMB about two minutes later, mid-animation.

XPAD's main loop includes a "game loader" that watches for SELECT + L3 + R3. Every 10 ms it calls
`cellPadInit` / `cellPadGetData` for every pad port, from XPAD's own thread, inside VSH, alongside
the XMB's own pad handling. With that block skipped, the console stays stable. XPAD's own USB
handling still works, and that path is what feeds the 8BitDo to the system.

The fix is a single branch in segment 0 of `xpad_vsh_autodiscovery.sprx` (the pinned v1.0.0 build):

```
0x8a38   419e0010   beq  cr7, +0x10     (original: enter the libpad poll block)
0x8a38   480002c4   b    0x8cfc         (patched: jump past it)
```

- It is written **in the console's memory only**, through webMAN's PS3MAPI, right after webMAN loads
  the plugin. That is before XPAD's 10-second start delay ends, so the poll never runs.
- The 24 bytes around the branch (`e80900002fa00000419e00107c00f8502ba0270f409d02b8`) are compared
  first. If they differ, for example because a different XPAD build is installed, **nothing is
  written** and the tool tells you to restart the PS3.
- No file on the PS3 is changed. Restarting the console removes the patch, which is why
  `ps3 start` is needed after every "Enable HEN".
- The patch was confirmed on the real console by reading it back (`480002c4`). After that, the
  XMB stayed responsive and the 8BitDo worked as a controller on the console. The plugin has run
  for more than 30 minutes without freezing; before the fix, the freeze came after about 2.

In the source, the patched branch is this check in `poll_manual_game_loader()`, which the compiler
inlines into XPAD's main thread:

```c
  usb_port = game_loader_usb_request_port;          /* SELECT+L3+R3 seen in XPAD's own USB data */
  if (usb_port != 0) { ... request_manual_game_load(); }

  if (game_loader_last_pad_poll != 0 &&             /* <- 0x8a30..0x8a44 */
      now - game_loader_last_pad_poll < GAME_LOADER_PAD_POLL_USEC) return;
  ...manual_loader_combo_down();                    /* cellPadGetInfo2 / cellPadGetData: skipped */
```

The patch turns that `if` into an unconditional `return`. The USB path above it still runs, so
**SELECT + L3 + R3 still loads the rumble module** for controllers XPAD drives itself, including
the 8BitDo. Only the fallback is lost: it reads the combo through libpad from controllers XPAD
doesn't drive itself (its log calls it "libpad BT"), for example a DualShock 3. Press the combo on
the 8BitDo.

The simulator applies the same change at the source level (`sim/build.sh` builds `ps3sim_fixed`)
and checks it end to end:
- the loader fires from the USB path (`manual loader: normalized USB combo accepted`);
- the rumble module is loaded into the running game;
- a game's rumble command reaches the receiver as an XInput rumble packet;
- no `manual loader: libpad …` line ever appears.

The unpatched build does log libpad polling, which shows that last check is meaningful.

The offsets come from decrypting the sprx with RPCS3 (`rpcs3 --decrypt`) and disassembling it with
rizin through REA (`tools/audit/prx_calls.py` lists the libpad call sites).

## What `ps3 start` sends to webMAN

Everything goes over webMAN MOD's HTTP server on port 80 (or the PS3MAPI server on 7887):

| Step | Request |
|---|---|
| Is XPAD already loaded? | `/ps3mapi.ps3?MODULE GETALLPRXID 16777984`, then `MODULE GETNAME 16777984 <id>` for each id (`16777984` = `0x01000300`, the VSH process). XPAD's module name is `XPADD` |
| Load it | `/vshplugin.ps3mapi?load_slot=0&prx=/dev_hdd0/plugins/ps3xpad/xpad_vsh_autodiscovery.sprx` (`load_slot=0` = first free VSH plugin slot) |
| Where is it? | `/ps3mapi.ps3?MODULE GETSEGMENTS 16777984 <id>`, which gives the segment 0 base address |
| Check the code | `/getmem.ps3mapi?proc=0x1000300&addr=<base+0x8a30>&len=24`. The bytes are read from the page's hidden `<textarea id="output">`. `MEMORY GET` over HTTP is not used because webMAN 1.47's hex output drops the high nibble |
| Patch | `/getmem.ps3mapi?proc=0x1000300&addr=<base+0x8a38>&val=480002c4` |
| Verify | the same read as "Check the code", on 4 bytes |

The code is in [`bitdo_ps3/ps3mapi.py`](../bitdo_ps3/ps3mapi.py). Every start is logged to
`captures/ps3_start_*.json`.

## Starting without a PC (`ps3 autorun`)

webMAN MOD runs `/dev_hdd0/autoexec.bat` as a script after its servers start, i.e. right after every
"Enable HEN". `ps3 install` writes one marked block into that file (unless `--no-autorun` is given);
`ps3 autorun` writes it again and `ps3 autorun --off` removes it (any other lines in the file are
kept byte for byte). The block waits for the XMB, loads the plugin, and logs every line of each boot
to `/dev_hdd0/tmp/8bitdo_ps3_autorun.log`. Conceptually it is:

```
/vshplugin.ps3mapi?load_slot=0&prx=/dev_hdd0/plugins/ps3xpad/xpad_vsh_autodiscovery.sprx
wait 2
/setmem.ps3mapi?proc=0x1000300&addr=0x900000&stop=0xc00000&find=<24-byte context>&offset=4&val=480002c4
/setmem.ps3mapi?proc=0x1000300&addr=0xc00000&stop=0x1400000&find=<24-byte context>&offset=4&val=480002c4
```

Two problems had to be solved for a script to work with no PC:

1. **Loading.** `/loadprx_ps3` really is gone, but `/vshplugin.ps3mapi?load_slot=0` loads into the
   first free VSH plugin slot and works from a script.
2. **Applying the fix without knowing the base address.** `ps3 start` asks webMAN for the segment
   base, then patches `<base+0x8a38>`. A batch script has no arithmetic, so instead of an address we
   search memory: `/setmem.ps3mapi` accepts `find=<hex>` and writes `val` at `match+offset`.
   The needle is the 24-byte context ([the freeze fix](#the-xmb-freeze-and-the-fix), starting 4
   bytes in so it begins with a digit — webMAN treats `find` as hex only when the first character is
   a digit), and `offset=4` lands on the `419e0010` branch. If the plugin is not present (already
   patched, or not loaded) the search simply finds nothing and writes nothing, so the block is safe
   to run more than once.
   The two lines scan `0x900000`–`0x1200000` in two chunks because the loaded segment base moves
   from boot to boot; the needle is long enough that a false match is not a concern.

Because a script cannot read a value back, this path trusts the search. `ps3 start` (from a PC)
still does the read-back-and-compare, so use it when you want the byte-for-byte confirmation.

## Audit of the plugin files

Microsoft Defender flags XPAD Revolution's **release zip** (`Trojan:Win32/Suschil!rfn`). That
verdict comes from machine learning and file reputation; the zip also bundles an optional Windows
viewer installer. **This project does not use the zip or any of its Windows programs.** It
downloads the five PS3 files from the repository's git tree at commit
[`0592973`](https://github.com/GabaRSk/XPAD-Revolution/tree/05929739baaab0e57b0d8ca3ad5c6407eccb19c9/plugin/ps3xpad)
and checks them against pinned SHA-256 hashes ([`bitdo_ps3/xpad_files.py`](../bitdo_ps3/xpad_files.py)).

Both `.sprx` files were decrypted with RPCS3 and checked against the published source
([`tools/audit/audit_prx.py`](../tools/audit/audit_prx.py), reports in [`docs/audit/`](audit/)):

- **System calls:** every one is either a call the source makes directly, or one that the SDK's
  thread, mutex, event-queue and timer functions compile to.
- **Libraries:** `cellUsbd`, `sysPrxForUser`, `sys_fs`, `sys_io`, `sys_net` (the telemetry port),
  `vshtask` (the on-screen notifications).
- **Strings:** 242 of the 272 string literals in the source are present in the binary. No network
  endpoints, and no paths to the system partitions (`/dev_flash`, `/dev_blind`, …).

## The simulated PS3

`sim/` is the "local environment" used to develop and test this without risking the console:

- XPAD Revolution's `src/ps3/main.c` is compiled for 32-bit big-endian PowerPC, which matches the
  byte order and pointer size of the PS3's PPU, and runs under `qemu-ppc` in WSL. It is built
  twice: as published, and with the freeze fix (the default, since that is what runs on the console).
- `sim/shim/` stands in for the parts of the PS3 the plugin talks to:
  - **USB driver layer (libusbd):** the extra-driver table with the retail size limit; probe,
    attach and detach on one USB thread, as on the PS3; asynchronous transfers.
  - **Pad layer (libpad):** 7 ports, plugin-inserted pads, and what a game reads at 60 Hz.
  - **System software (VSH):** the export table at `0x1008C`, so the plugin's own lookups find
    notifications, malloc and free as on the console.
  - **Files (cellFs):** mapped to `sim/ps3root/dev_hdd0/…`; the plugin writes its real `xpad.log` there.
- The simulated USB port is bridged over TCP to Windows ([`bitdo_ps3/bridge.py`](../bitdo_ps3/bridge.py)). On the other end is either:
  - a virtual receiver, using the descriptors captured from the real one; or
  - your real receiver, through libusb + UsbDk (`8bitdo-ps3 test --real`).
- Each run grades every button, axis and trigger as a game would read it. It writes a report, an
  event log, the plugin log and a USB capture (`ps3_usb.pcap`, USBPcap format for Wireshark) to
  `captures/sim_*/`.

What it does **not** reproduce: HEN itself, the XMB's own threads, the SELF loader, and the XMB
freeze above, which depends on the real system software.

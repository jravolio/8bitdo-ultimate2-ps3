"""8bitdo-ps3: use an 8BitDo Ultimate 2 Bluetooth controller (2.4G receiver) on a HEN PS3 and on PC."""
from pathlib import Path

__version__ = "1.0.0"

ROOT = Path(__file__).resolve().parents[1]
SIM_DIR = ROOT / "sim"
SIM_BINARY = SIM_DIR / "build" / "ps3sim"
SIM_ROOT = SIM_DIR / "ps3root"
XPAD_REPO = ROOT / "refs" / "XPAD-Revolution"
PS3_PAYLOAD = ROOT / "ps3" / "plugins" / "ps3xpad"
CAPTURES = ROOT / "captures"
BACKUPS = ROOT / "backups"

VID_8BITDO = 0x2DC8
PID_RECEIVER_IDLE = 0x3109      # receiver, no controller linked (vendor config channel only)
PID_RECEIVER_XINPUT = 0x310B    # receiver with the controller linked, XInput mode (PC + PS3/XPAD)
PRODUCT_ULTIMATE2_BT_RR = 0x6011  # receiver product id reported over the vendor channel
VID_NINTENDO, PID_SWITCH_PRO = 0x057E, 0x2009
VID_SONY, PID_DS3 = 0x054C, 0x0268

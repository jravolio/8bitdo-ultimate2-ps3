"""Audit a decrypted PS3 PRX (from `rpcs3 --decrypt`) with REA + rizin, against its published source.

  python -I audit_prx.py <decrypted.prx> <plugin main.c> <report.json>

Checks:
  * every `sc` (lv2 syscall) instruction and the syscall number loaded into r11 before it
  * every string in the binary, and which source string literals are / are not present
  * flags syscalls outside the set the source uses and strings that look like network
    endpoints or writes to system partitions (/dev_flash, /dev_blind, ...)
"""
import json
import os
import re
import subprocess
import sys

REA = os.environ.get("REA_BIN", "rea")
ANSI = re.compile(r"\x1b\[[0-9;]*m")
CR = chr(13)
STRING_ROW = re.compile(
    r"0x[0-9a-f]+\s+0x[0-9a-f]+\s+\d+\s+\d+\s+(?:\S+\s+)?(?:ascii|utf8|utf16le|utf16be|utf32le|utf32be|wide)\s+(.*)$")


def rizin(prx, cmd):
    out = subprocess.run([REA, "execute-rizin-command", prx, "e scr.color=0; e asm.bytes=false; " + cmd,
                          "--format", "json"], capture_output=True, text=True, encoding="utf-8",
                         errors="replace", shell=os.name == "nt")
    doc = json.loads(out.stdout)
    res = doc.get("normalized_result") or {}
    return ANSI.sub("", res.get("stdout", "")).replace(CR, ""), doc.get("evidence_id")


def main(prx, source, report):
    src = open(source, encoding="utf-8", errors="replace").read()
    src_syscalls = sorted({int(n, 0) for n in re.findall(r"system_call_\d\(\s*(0x[0-9a-fA-F]+|\d+)", src)})
    src_strings = set(re.findall(r'"((?:[^"\\\n]|\\.){6,})"', src))

    hits_txt, ev_hits = rizin(prx, "/x 44000002")
    hits = re.findall(r"^(0x[0-9a-f]+) \d+ hit", hits_txt, re.M)
    syscalls = []
    for h in hits:
        dis, _ = rizin(prx, f"pd 8 @ {h}-28")
        nums = re.findall(r"li\s+r11,\s*(0x[0-9a-f]+|-?\d+)", dis)
        syscalls.append({"addr": h, "num": int(nums[-1], 0) if nums else None})
    used = sorted({s["num"] for s in syscalls if s["num"] is not None})

    strs_txt, ev_strs = rizin(prx, "izz")
    bin_strings = [m.group(1) for m in map(STRING_ROW.match, strs_txt.splitlines()) if m]
    blob = "\n".join(bin_strings)

    def present(s):
        s = s.replace('\\"', '"')
        return s in blob or s.split("\\")[0] in blob

    src_found = sorted(s for s in src_strings if present(s))
    src_missing = sorted(s for s in src_strings if not present(s))
    suspicious = [s for s in bin_strings if re.search(
        r"https?://|ftp://|\b\d{1,3}(\.\d{1,3}){3}\b|/dev_flash|/dev_blind|/dev_rewrite|/dev_rebug|lv1|syscon", s, re.I)]
    unexpected = [n for n in used if n not in src_syscalls]

    result = {
        "prx": prx,
        "rea_evidence": {"sc_search": ev_hits, "strings": ev_strs},
        "syscall_sites": syscalls,
        "syscall_sites_unresolved": [s["addr"] for s in syscalls if s["num"] is None],
        "syscalls_used": used,
        "syscalls_in_source": src_syscalls,
        "syscalls_not_in_source": unexpected,
        "binary_string_count": len(bin_strings),
        "source_strings": len(src_strings),
        "source_strings_found_in_binary": len(src_found),
        "source_strings_missing_sample": src_missing[:15],
        "suspicious_strings": suspicious,
        "library_names": sorted({s for s in bin_strings if re.fullmatch(r"(cell|sys|vsh|allocator|stdc|netctl)[A-Za-z0-9_]{1,24}", s)}),
    }
    with open(report, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1)
    print(json.dumps({k: v for k, v in result.items() if k != "syscall_sites"}, indent=1))


if __name__ == "__main__":
    main(*sys.argv[1:4])

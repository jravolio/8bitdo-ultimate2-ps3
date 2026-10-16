"""List import trampolines of a decrypted PS3 PRX and the call sites of chosen imports."""
import struct, sys
NAMES = {0x1cf98800: "cellPadInit", 0x8b72cda1: "cellPadGetData", 0xa703a51d: "cellPadGetInfo2",
         0xbafd6409: "cellPadLddDataInsert", 0x578e3c98: "cellPadSetPortSetting", 0x8b8231e5: "cellPadLddGetPortNo",
         0xe442faa8: "cellPadLddUnregisterController"}
def load(path):
    b = open(path, "rb").read()
    phoff, = struct.unpack_from(">Q", b, 0x20); phnum, = struct.unpack_from(">H", b, 0x38)
    phs = [struct.unpack_from(">IIQQQQQQ", b, phoff + i * 0x38) for i in range(phnum)]
    segs = [(off, va, fs) for pt, fl, off, va, pa, fs, ms, al in phs if pt == 1]
    return b, phs, segs
def main(path, *want):
    b, phs, segs = load(path)
    v2o = lambda v, end=False: next((off + v - va for off, va, fs in segs if va <= v < va + fs or (end and v == va + fs)), None)
    o2v = lambda o: next((va + o - off for off, va, fs in segs if off <= o < off + fs), None)
    mi = phs[0][4] if b[phs[0][4] + 4:phs[0][4] + 8].isalpha() else phs[0][2] + phs[0][4]
    ims, ime = struct.unpack_from(">II", b, mi + 44)
    slot = {}
    o, end = v2o(ims), v2o(ime, True)
    while o < end:
        size, _, _, _, nfunc = struct.unpack_from(">BBHHH", b, o)
        name_ptr, nid_ptr, stub_ptr = struct.unpack_from(">III", b, o + 0x10)
        lib = b[v2o(name_ptr):v2o(name_ptr) + 32].split(b"\0")[0].decode()
        for i in range(nfunc):
            nid, = struct.unpack_from(">I", b, v2o(nid_ptr) + 4 * i)
            slot[stub_ptr + 4 * i] = f"{lib}:{NAMES.get(nid, hex(nid))}"
        o += size
    off, va, fs = segs[0]
    tramp = {}
    for o in range(off, off + fs - 12, 4):
        i1, i2, i3 = struct.unpack_from(">III", b, o)
        if i1 == 0x39800000 and i2 >> 16 == 0x658c and i3 >> 16 == 0x818c:
            lo = i3 & 0xffff
            addr = ((i2 & 0xffff) << 16) + (lo - 0x10000 if lo & 0x8000 else lo)
            tramp[o2v(o)] = slot.get(addr, hex(addr))
    for t, n in sorted(tramp.items()):
        print(f"trampoline 0x{t:x} -> {n}")
    targets = {t for t, n in tramp.items() if any(n.endswith(w) for w in want)}
    for o in range(off, off + fs, 4):
        ins, = struct.unpack_from(">I", b, o)
        if ins >> 26 == 18 and ins & 3 == 1:
            li = ins & 0x03fffffc
            li -= 0x04000000 if li & 0x02000000 else 0
            if o2v(o) + li in targets:
                print(f"  call at 0x{o2v(o):x} -> {tramp[o2v(o) + li]}")
if __name__ == "__main__":
    main(*sys.argv[1:])

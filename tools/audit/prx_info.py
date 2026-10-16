"""Print SELF header facts (.sprx) and the import table (decrypted .prx) of a PS3 PRX."""
import re, struct, sys
NIDS = {0x1cf98800: "cellPadInit", 0x8b72cda1: "cellPadGetData", 0xa703a51d: "cellPadGetInfo2", 0x578e3c98: "cellPadSetPortSetting",
        0xbafd6409: "cellPadLddDataInsert", 0x8b8231e5: "cellPadLddGetPortNo", 0xe442faa8: "cellPadLddUnregisterController",
        0x20a97ba2: "cellPadLddRegisterController", 0xf65544ee: "cellPadSetActDirect", 0x4d9b75d5: "cellPadEnd"}
def self_info(path):
    b = open(path, "rb").read()
    keyrev, = struct.unpack_from(">H", b, 8); appinfo, = struct.unpack_from(">Q", b, 0x28)
    auth, = struct.unpack_from(">Q", b, appinfo)
    return f"keyrev=0x{keyrev:x} auth=0x{auth:016x}"
def imports(path):
    b = open(path, "rb").read()
    phoff, = struct.unpack_from(">Q", b, 0x20); phnum, = struct.unpack_from(">H", b, 0x38)
    phs = [struct.unpack_from(">IIQQQQQQ", b, phoff + i * 0x38) for i in range(phnum)]
    def v2o(v, end=False):
        for pt, fl, off, va, pa, fs, ms, al in phs:
            if pt == 1 and (va <= v < va + fs or (end and v == va + fs)):
                return off + v - va
    for cand in (phs[0][4], phs[0][2] + phs[0][4]):
        ims, ime = struct.unpack_from(">II", b, cand + 44)
        if 0 < ims < ime < 0x1000000 and v2o(ims) is not None:
            break
    out, o, end = {}, v2o(ims), v2o(ime, True)
    while o is not None and o < end:
        size, _, _, _, nfunc = struct.unpack_from(">BBHHH", b, o)
        name_ptr, nid_ptr = struct.unpack_from(">II", b, o + 0x10)
        lib = b[v2o(name_ptr):v2o(name_ptr) + 32].split(b"\0")[0].decode(errors="replace")
        out[lib] = [NIDS.get(n, hex(n)) for n in (struct.unpack_from(">I", b, v2o(nid_ptr) + 4 * i)[0] for i in range(nfunc))]
        o += size
    return out
for sprx in sys.argv[1:]:
    prx = re.sub(r"\.sprx$", ".prx", sprx)
    print(f"== {sprx}\n   {self_info(sprx)}")
    try:
        for lib, fns in imports(prx).items():
            print(f"   {lib:14s} {', '.join(fns) if lib == 'sys_io' else len(fns)}")
    except Exception as e:
        print("   imports:", e)

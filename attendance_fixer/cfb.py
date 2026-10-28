"""Minimal reader/writer for OLE2 Compound File Binary (the container of .xls files).

Only flat files (streams directly under the root) are supported, which is what the
attendance machine produces: Workbook, GLOG, CompObj and the two summary streams.
"""
import struct

SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
FREESECT = 0xFFFFFFFF
ENDOFCHAIN = 0xFFFFFFFE
FATSECT = 0xFFFFFFFD
DIFSECT = 0xFFFFFFFC
NOSTREAM = 0xFFFFFFFF
MINI_CUTOFF = 4096


class CompoundFile(object):
    def __init__(self):
        self.streams = []          # list of [name, bytes], in original directory order
        self.root_clsid = b"\x00" * 16

    def get(self, name):
        for n, data in self.streams:
            if n == name:
                return data
        raise KeyError(name)

    def set(self, name, data):
        for item in self.streams:
            if item[0] == name:
                item[1] = data
                return
        self.streams.append([name, data])

    def names(self):
        return [n for n, _ in self.streams]


def read(path):
    with open(path, "rb") as f:
        raw = f.read()
    return loads(raw)


def loads(raw):
    if raw[:8] != SIGNATURE:
        raise ValueError("Not an Excel 97-2003 (.xls) file")
    sector_shift, mini_shift = struct.unpack_from("<HH", raw, 30)
    ssz = 1 << sector_shift
    msz = 1 << mini_shift
    (n_fat, first_dir, _tx, mini_cutoff, first_minifat, n_minifat,
     first_difat, n_difat) = struct.unpack_from("<IIIIIIII", raw, 44)
    difat = list(struct.unpack_from("<109I", raw, 76))
    sect = first_difat
    while sect not in (ENDOFCHAIN, FREESECT) and n_difat > 0:
        off = (sect + 1) * ssz
        vals = struct.unpack_from("<%dI" % (ssz // 4), raw, off)
        difat.extend(vals[:-1])
        sect = vals[-1]
        n_difat -= 1
    fat_sectors = [s for s in difat if s < 0xFFFFFFFA][:n_fat]

    def sector(i):
        off = (i + 1) * ssz
        return raw[off:off + ssz]

    fat = []
    for s in fat_sectors:
        fat.extend(struct.unpack("<%dI" % (ssz // 4), sector(s)))

    def chain(start, table):
        out = []
        seen = set()
        while start < 0xFFFFFFFA:
            if start in seen or start >= len(table):
                raise ValueError("Corrupt sector chain")
            seen.add(start)
            out.append(start)
            start = table[start]
        return out

    dir_data = b"".join(sector(s) for s in chain(first_dir, fat))
    entries = []
    for i in range(len(dir_data) // 128):
        e = dir_data[i * 128:(i + 1) * 128]
        name_len = struct.unpack_from("<H", e, 64)[0]
        name = e[:max(0, name_len - 2)].decode("utf-16-le")
        etype, _color = struct.unpack_from("<BB", e, 66)
        left, right, child = struct.unpack_from("<III", e, 68)
        clsid = e[80:96]
        start, size = struct.unpack_from("<IQ", e, 116)
        if sector_shift == 9:
            size &= 0xFFFFFFFF
        entries.append(dict(name=name, type=etype, left=left, right=right,
                            child=child, clsid=clsid, start=start, size=size))

    root = entries[0]
    minifat = []
    if n_minifat:
        for s in chain(first_minifat, fat):
            minifat.extend(struct.unpack("<%dI" % (ssz // 4), sector(s)))
    mini_stream = b"".join(sector(s) for s in chain(root["start"], fat)) if root["size"] else b""

    def read_stream(e):
        if e["size"] < mini_cutoff:
            data = b"".join(mini_stream[s * msz:(s + 1) * msz] for s in chain(e["start"], minifat))
        else:
            data = b"".join(sector(s) for s in chain(e["start"], fat))
        return data[:e["size"]]

    cf = CompoundFile()
    cf.root_clsid = root["clsid"]

    # Walk the red-black tree under the root in index order (stable, original order).
    order = []

    def walk(idx):
        if idx == NOSTREAM or idx >= len(entries):
            return
        e = entries[idx]
        walk(e["left"])
        order.append(idx)
        walk(e["right"])

    walk(root["child"])
    for idx in sorted(order):
        e = entries[idx]
        if e["type"] == 1:
            raise ValueError("Unsupported .xls layout (contains sub-storage '%s')" % e["name"])
        if e["type"] == 2:
            cf.streams.append([e["name"], read_stream(e)])
    return cf


def _name_key(name):
    return (len(name), name.upper())


def dumps(cf):
    ssz = 512
    msz = 64
    streams = cf.streams
    n = len(streams)

    # --- mini stream (streams smaller than the cutoff)
    mini_blob = bytearray()
    minifat = []
    starts = [None] * n
    for i, (name, data) in enumerate(streams):
        if len(data) < MINI_CUTOFF:
            if not data:
                starts[i] = ENDOFCHAIN
                continue
            count = (len(data) + msz - 1) // msz
            first = len(minifat)
            starts[i] = first
            for k in range(count):
                minifat.append(first + k + 1 if k < count - 1 else ENDOFCHAIN)
            mini_blob += data + b"\x00" * (count * msz - len(data))

    # --- directory tree (all black, balanced binary search tree)
    order = sorted(range(n), key=lambda i: _name_key(streams[i][0]))
    left = [NOSTREAM] * n
    right = [NOSTREAM] * n

    def build(lo, hi):
        if lo > hi:
            return NOSTREAM
        mid = (lo + hi) // 2
        idx = order[mid]
        left[idx] = build(lo, mid - 1)
        right[idx] = build(mid + 1, hi)
        return idx + 1  # +1: entry 0 is the root

    root_child = build(0, n - 1)

    n_dir_entries = n + 1
    n_dir_sectors = (n_dir_entries * 128 + ssz - 1) // ssz
    n_minifat_sectors = (len(minifat) * 4 + ssz - 1) // ssz
    n_mini_sectors = (len(mini_blob) + ssz - 1) // ssz
    big_counts = []
    for name, data in streams:
        big_counts.append((len(data) + ssz - 1) // ssz if len(data) >= MINI_CUTOFF else 0)
    content = n_dir_sectors + n_minifat_sectors + n_mini_sectors + sum(big_counts)

    n_fat = 1
    while n_fat * (ssz // 4) < content + n_fat:
        n_fat += 1
    if n_fat > 109:
        raise ValueError("File too large for this writer")

    fat = []
    pos = [0]

    def alloc(count):
        if count == 0:
            return ENDOFCHAIN
        first = pos[0]
        for k in range(count):
            fat.append(first + k + 1 if k < count - 1 else ENDOFCHAIN)
        pos[0] += count
        return first

    for _ in range(n_fat):
        fat.append(FATSECT)
    pos[0] = n_fat
    dir_start = alloc(n_dir_sectors)
    minifat_start = alloc(n_minifat_sectors) if n_minifat_sectors else ENDOFCHAIN
    mini_start = alloc(n_mini_sectors) if n_mini_sectors else ENDOFCHAIN
    for i, (name, data) in enumerate(streams):
        if big_counts[i]:
            starts[i] = alloc(big_counts[i])
    total = len(fat)
    fat += [FREESECT] * (n_fat * (ssz // 4) - total)

    def dir_entry(name, etype, l, r, child, clsid, start, size):
        enc = name.encode("utf-16-le")
        if len(enc) > 62:
            raise ValueError("Stream name too long: %r" % name)
        e = enc + b"\x00" * (64 - len(enc))
        e += struct.pack("<HBB", len(enc) + 2 if name or etype else 0, etype, 1)
        e += struct.pack("<III", l, r, child)
        e += clsid + struct.pack("<I", 0) + b"\x00" * 16
        e += struct.pack("<IQ", start, size)
        return e

    dirs = dir_entry("Root Entry", 5, NOSTREAM, NOSTREAM, root_child, cf.root_clsid,
                     mini_start if mini_blob else ENDOFCHAIN, len(mini_blob))
    for i, (name, data) in enumerate(streams):
        dirs += dir_entry(name, 2, left[i], right[i], NOSTREAM, b"\x00" * 16,
                          starts[i] if data else ENDOFCHAIN, len(data))
    empty = b"\x00" * 64 + struct.pack("<HBB", 0, 0, 0) + struct.pack("<III", NOSTREAM, NOSTREAM, NOSTREAM) \
        + b"\x00" * 36 + struct.pack("<IQ", 0, 0)
    while len(dirs) < n_dir_sectors * ssz:
        dirs += empty

    header = SIGNATURE + b"\x00" * 16
    header += struct.pack("<HHHHH", 0x3E, 3, 0xFFFE, 9, 6)
    header += b"\x00" * 6
    header += struct.pack("<IIIIIIIII", 0, n_fat, dir_start, 0, MINI_CUTOFF,
                          minifat_start, n_minifat_sectors, ENDOFCHAIN, 0)
    difat = list(range(n_fat)) + [FREESECT] * (109 - n_fat)
    header += struct.pack("<109I", *difat)
    assert len(header) == 512

    out = bytearray(header)
    out += struct.pack("<%dI" % len(fat), *fat)
    out += dirs
    if n_minifat_sectors:
        mf = minifat + [FREESECT] * (n_minifat_sectors * (ssz // 4) - len(minifat))
        out += struct.pack("<%dI" % len(mf), *mf)
    if n_mini_sectors:
        out += bytes(mini_blob) + b"\x00" * (n_mini_sectors * ssz - len(mini_blob))
    for i, (name, data) in enumerate(streams):
        if big_counts[i]:
            out += data + b"\x00" * (big_counts[i] * ssz - len(data))
    return bytes(out)


def write(cf, path):
    data = dumps(cf)
    with open(path, "wb") as f:
        f.write(data)

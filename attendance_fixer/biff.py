"""Just enough BIFF8 (Excel 97-2003) to read and rewrite cell values of one sheet.

Everything that is not a cell (formatting, the sheet lock, drawings, the GLOG
stream...) is copied byte for byte, so the saved file looks like the original
machine export.
"""
import struct

BOF = 0x0809
EOF = 0x000A
BOUNDSHEET = 0x0085
SST = 0x00FC
CONTINUE = 0x003C
DIMENSIONS = 0x0200
ROW = 0x0208
DBCELL = 0x00D7
INDEX = 0x020B
MERGE = 0x00E5

BLANK = 0x0201
MULBLANK = 0x00BE
LABEL = 0x0204
LABELSST = 0x00FD
RK = 0x027E
MULRK = 0x00BD
NUMBER = 0x0203
BOOLERR = 0x0205
FORMULA = 0x0006
STRING = 0x0207
RSTRING = 0x00D6

CELL_TYPES = {BLANK, MULBLANK, LABEL, LABELSST, RK, MULRK, NUMBER, BOOLERR, FORMULA, RSTRING}
UNSUPPORTED = {0x04BC, 0x0221, 0x0236}  # SHRFMLA, ARRAY, TABLE


def split_records(data):
    recs = []
    i = 0
    while i + 4 <= len(data):
        t, l = struct.unpack_from("<HH", data, i)
        recs.append([t, data[i + 4:i + 4 + l]])
        i += 4 + l
    return recs


def join_records(recs):
    out = bytearray()
    for t, body in recs:
        out += struct.pack("<HH", t, len(body)) + body
    return bytes(out)


def rk_value(rk):
    if rk & 2:
        v = rk >> 2
        if v & 0x20000000:
            v -= 0x40000000
        v = float(v)
    else:
        v = struct.unpack("<d", struct.pack("<Q", (rk & 0xFFFFFFFC) << 32))[0]
    if rk & 1:
        v /= 100.0
    return v


def read_unicode(data, pos, cch_size=2):
    """XLUnicodeString without CONTINUE handling. Returns (text, new_pos)."""
    if cch_size == 2:
        cch = struct.unpack_from("<H", data, pos)[0]
    else:
        cch = data[pos]
    pos += cch_size
    flags = data[pos]
    pos += 1
    rich = ext = 0
    if flags & 8:
        rich = struct.unpack_from("<H", data, pos)[0]
        pos += 2
    if flags & 4:
        ext = struct.unpack_from("<I", data, pos)[0]
        pos += 4
    if flags & 1:
        text = data[pos:pos + cch * 2].decode("utf-16-le")
        pos += cch * 2
    else:
        text = data[pos:pos + cch].decode("latin-1")
        pos += cch
    pos += rich * 4 + ext
    return text, pos


def encode_unicode(text):
    try:
        raw = text.encode("latin-1")
        return struct.pack("<HB", len(text), 0) + raw
    except UnicodeEncodeError:
        return struct.pack("<HB", len(text), 1) + text.encode("utf-16-le")


def parse_sst(body, continues):
    """Parse the shared string table, honouring CONTINUE records."""
    chunks = [body] + continues
    ci = 0
    data = chunks[0]
    pos = 8
    _total, unique = struct.unpack_from("<II", data, 0)
    strings = []

    def ensure(n):
        nonlocal ci, data, pos
        while pos + n > len(data) and pos >= len(data) and ci + 1 < len(chunks):
            ci += 1
            data = chunks[ci]
            pos = 0

    for _ in range(unique):
        ensure(3)
        if pos >= len(data):
            break
        cch = struct.unpack_from("<H", data, pos)[0]
        flags = data[pos + 2]
        pos += 3
        rich = ext = 0
        if flags & 8:
            ensure(2)
            rich = struct.unpack_from("<H", data, pos)[0]
            pos += 2
        if flags & 4:
            ensure(4)
            ext = struct.unpack_from("<I", data, pos)[0]
            pos += 4
        high = flags & 1
        parts = []
        remaining = cch
        while remaining > 0:
            if pos >= len(data):
                ci += 1
                data = chunks[ci]
                high = data[0] & 1
                pos = 1
            width = 2 if high else 1
            avail = (len(data) - pos) // width
            take = min(avail, remaining)
            seg = data[pos:pos + take * width]
            parts.append(seg.decode("utf-16-le") if high else seg.decode("latin-1"))
            pos += take * width
            remaining -= take
        skip = rich * 4 + ext
        while skip > 0:
            if pos >= len(data):
                ci += 1
                data = chunks[ci]
                pos = 0
            take = min(skip, len(data) - pos)
            pos += take
            skip -= take
        strings.append("".join(parts))
    return strings


class Cell(object):
    __slots__ = ("kind", "xf", "value", "raw")

    def __init__(self, kind, xf, value=None, raw=None):
        self.kind = kind      # 'blank', 'text', 'number', 'raw'
        self.xf = xf
        self.value = value
        self.raw = raw        # original records, written back verbatim while the cell is unchanged


class Sheet(object):
    """Cell table of one worksheet plus the surrounding records."""

    def __init__(self, name, records, sst):
        self.name = name
        self.head = []      # records before the cell table
        self.rows = {}      # row -> ROW record body
        self.cells = {}     # (row, col) -> Cell
        self.tail = []      # records after the cell table
        self._parse(records, sst)

    def _parse(self, records, sst):
        stage = 0
        i = 0
        while i < len(records):
            t, body = records[i]
            if t in UNSUPPORTED:
                raise ValueError("Unsupported content in sheet (shared formulas)")
            is_cell = t in CELL_TYPES or t in (ROW, DBCELL, INDEX, STRING)
            if stage == 0 and (t in CELL_TYPES or t == ROW):
                stage = 1
            if stage == 1 and not is_cell:
                stage = 2
            if stage == 0:
                if t != INDEX:
                    self.head.append([t, body])
            elif stage == 2:
                self.tail.append([t, body])
            else:
                if t == ROW:
                    self.rows[struct.unpack_from("<H", body)[0]] = body
                elif t in (DBCELL, INDEX):
                    pass
                elif t == BLANK:
                    r, c, xf = struct.unpack_from("<HHH", body)
                    self.cells[(r, c)] = Cell("blank", xf, None, [[t, body]])
                elif t == MULBLANK:
                    r, c1 = struct.unpack_from("<HH", body)
                    n = (len(body) - 6) // 2
                    for k in range(n):
                        xf = struct.unpack_from("<H", body, 4 + 2 * k)[0]
                        self.cells[(r, c1 + k)] = Cell("blank", xf)
                elif t in (LABEL, RSTRING):
                    r, c, xf = struct.unpack_from("<HHH", body)
                    text, _ = read_unicode(body, 6)
                    self.cells[(r, c)] = Cell("text", xf, text, [[t, body]])
                elif t == LABELSST:
                    r, c, xf, idx = struct.unpack_from("<HHHI", body)
                    self.cells[(r, c)] = Cell("text", xf, sst[idx] if idx < len(sst) else "", [[t, body]])
                elif t == RK:
                    r, c, xf, rk = struct.unpack_from("<HHHI", body)
                    self.cells[(r, c)] = Cell("number", xf, rk_value(rk), [[t, body]])
                elif t == MULRK:
                    r, c1 = struct.unpack_from("<HH", body)
                    n = (len(body) - 6) // 6
                    for k in range(n):
                        xf, rk = struct.unpack_from("<HI", body, 4 + 6 * k)
                        self.cells[(r, c1 + k)] = Cell("number", xf, rk_value(rk))
                elif t == NUMBER:
                    r, c, xf, v = struct.unpack_from("<HHHd", body)
                    self.cells[(r, c)] = Cell("number", xf, v, [[t, body]])
                elif t in (FORMULA, BOOLERR):
                    r, c, xf = struct.unpack_from("<HHH", body)
                    raw = [[t, body]]
                    if t == FORMULA and i + 1 < len(records) and records[i + 1][0] == STRING:
                        raw.append(records[i + 1])
                        i += 1
                    self.cells[(r, c)] = Cell("raw", xf, None, raw)
            i += 1

    # ---- reading helpers
    def value(self, r, c):
        cell = self.cells.get((r, c))
        if cell is None or cell.kind in ("blank", "raw"):
            return ""
        return cell.value

    def max_row(self):
        rows = [r for r, _ in self.cells] + list(self.rows)
        return max(rows) if rows else -1

    def max_col(self):
        cols = [c for _, c in self.cells]
        return max(cols) if cols else -1

    # ---- editing helpers
    def set_text(self, r, c, text, xf=None):
        old = self.cells.get((r, c))
        if xf is None:
            xf = old.xf if old is not None else 15
        if text == "" or text is None:
            self.cells[(r, c)] = Cell("blank", xf)
        else:
            self.cells[(r, c)] = Cell("text", xf, text)

    def remove(self, r, c):
        self.cells.pop((r, c), None)

    def insert_rows(self, at, count):
        """Insert `count` empty rows before row `at`, shifting everything below down."""
        if count <= 0:
            return
        moved = {}
        for (r, c), cell in self.cells.items():
            if r >= at:
                r += count
                for rec in cell.raw or []:
                    if rec[0] != STRING:
                        rec[1] = struct.pack("<H", r) + rec[1][2:]
            moved[(r, c)] = cell
        self.cells = moved
        rows = {}
        for r, body in self.rows.items():
            if r >= at:
                r += count
                body = struct.pack("<H", r) + body[2:]
            rows[r] = body
        self.rows = rows
        for rec in self.tail:
            if rec[0] == MERGE:
                body = bytearray(rec[1])
                n = struct.unpack_from("<H", body)[0]
                for k in range(n):
                    off = 2 + 8 * k
                    r1, r2 = struct.unpack_from("<HH", body, off)
                    if r1 >= at:
                        r1 += count
                    if r2 >= at:
                        r2 += count
                    struct.pack_into("<HH", body, off, r1, r2)
                rec[1] = bytes(body)

    # ---- writing
    def _cell_records(self):
        out = []
        by_row = {}
        for (r, c), cell in self.cells.items():
            by_row.setdefault(r, []).append((c, cell))
        for r in sorted(by_row):
            items = sorted(by_row[r], key=lambda x: x[0])
            k = 0
            while k < len(items):
                c, cell = items[k]
                if cell.raw is not None:
                    out.extend([list(x) for x in cell.raw])
                    k += 1
                    continue
                if cell.kind == "blank":
                    run = [cell.xf]
                    j = k + 1
                    while (j < len(items) and items[j][1].kind == "blank" and items[j][1].raw is None
                           and items[j][0] == c + len(run)):
                        run.append(items[j][1].xf)
                        j += 1
                    if len(run) == 1:
                        out.append([BLANK, struct.pack("<HHH", r, c, run[0])])
                    else:
                        out.append([MULBLANK, struct.pack("<HH", r, c)
                                    + struct.pack("<%dH" % len(run), *run)
                                    + struct.pack("<H", c + len(run) - 1)])
                    k = j
                    continue
                if cell.kind == "text":
                    out.append([LABEL, struct.pack("<HHH", r, c, cell.xf) + encode_unicode(cell.value)])
                elif cell.kind == "number":
                    v = float(cell.value)
                    if v == int(v) and -(1 << 29) <= v < (1 << 29):
                        out.append([RK, struct.pack("<HHHI", r, c, cell.xf, (int(v) << 2 | 2) & 0xFFFFFFFF)])
                    else:
                        out.append([NUMBER, struct.pack("<HHHd", r, c, cell.xf, v)])
                k += 1
        return out

    def records(self):
        recs = []
        max_r = self.max_row()
        max_c = self.max_col()
        for t, body in self.head:
            if t == DIMENSIONS and len(body) >= 12:
                r1, r2, c1, c2 = struct.unpack_from("<IIHH", body)
                if max_r + 1 > r2 or max_c + 1 > c2:
                    body = struct.pack("<IIHH", r1, max(r2, max_r + 1), c1, max(c2, max_c + 1)) + body[12:]
            recs.append([t, body])
        for r in sorted(self.rows):
            recs.append([ROW, self.rows[r]])
        recs.extend(self._cell_records())
        recs.extend(self.tail)
        return recs


class Workbook(object):
    def __init__(self, stream):
        recs = split_records(stream)
        # Globals substream
        i = 0
        self.globals = []
        while i < len(recs):
            self.globals.append(recs[i])
            if recs[i][0] == EOF:
                i += 1
                break
            i += 1
        self.sst = []
        sheet_names = []
        for k, (t, body) in enumerate(self.globals):
            if t == SST:
                conts = []
                j = k + 1
                while j < len(self.globals) and self.globals[j][0] == CONTINUE:
                    conts.append(self.globals[j][1])
                    j += 1
                self.sst = parse_sst(body, conts)
            elif t == BOUNDSHEET:
                name, _ = read_unicode(body, 6, cch_size=1)
                sheet_names.append(name)
        # Sheet substreams (raw), in stream order
        self.sheet_raw = []
        while i < len(recs):
            start = i
            depth = 0
            while i < len(recs):
                if recs[i][0] == BOF:
                    depth += 1
                elif recs[i][0] == EOF:
                    depth -= 1
                    if depth == 0:
                        i += 1
                        break
                i += 1
            self.sheet_raw.append(recs[start:i])
        self.sheet_names = sheet_names
        self._sheets = {}

    def sheet(self, index):
        if index not in self._sheets:
            self._sheets[index] = Sheet(self.sheet_names[index], self.sheet_raw[index], self.sst)
        return self._sheets[index]

    def serialize(self):
        sheet_recs = []
        for idx, raw in enumerate(self.sheet_raw):
            if idx in self._sheets:
                sheet_recs.append(self._sheets[idx].records())
            else:
                sheet_recs.append(raw)
        globals_bytes = join_records(self.globals)
        offset = len(globals_bytes)
        positions = []
        blobs = []
        for recs in sheet_recs:
            positions.append(offset)
            blob = join_records(recs)
            blobs.append(blob)
            offset += len(blob)
        out = bytearray()
        bs = 0
        for t, body in self.globals:
            if t == BOUNDSHEET and bs < len(positions):
                body = struct.pack("<I", positions[bs]) + body[4:]
                bs += 1
            out += struct.pack("<HH", t, len(body)) + body
        for blob in blobs:
            out += blob
        return bytes(out)

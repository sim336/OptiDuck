import struct, sys

path = r"E:\optiDuck\microduck_rl\logs\rsl_rl\velocity\2026-09-13_21-54-46_velocity\events.out.tfevents.1789307693.WenAndLiu.20380.0"

def read_varint(buf, pos):
    result = 0
    shift = 0
    while True:
        b = buf[pos]
        pos += 1
        result |= (b & 0x7f) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7

def read_fixed32(buf, pos):
    return struct.unpack_from('<f', buf, pos)[0], pos + 4

def read_fixed64(buf, pos):
    return struct.unpack_from('<d', buf, pos)[0], pos + 8

def parse_fields(buf, pos, end, handlers):
    # returns dict of field_number -> value for fields we care about
    out = {}
    while pos < end:
        tag, pos = read_varint(buf, pos)
        field = tag >> 3
        wt = tag & 7
        if wt == 0:
            v, pos = read_varint(buf, pos)
        elif wt == 1:
            v, pos = read_fixed64(buf, pos)
        elif wt == 2:
            ln, pos = read_varint(buf, pos)
            v = buf[pos:pos+ln]
            pos += ln
        elif wt == 5:
            v, pos = read_fixed32(buf, pos)
        else:
            break
        out[field] = v
    return out, pos

data = open(path, 'rb').read()
n = len(data)
pos = 0
records = []
while pos + 12 <= n:
    length = struct.unpack_from('<Q', data, pos)[0]
    pos += 8
    pos += 4  # crc
    payload = data[pos:pos+length]
    pos += length
    end = len(payload)
    p = 0
    fields = {}
    while p < end:
        tag, p = read_varint(payload, p)
        field = tag >> 3
        wt = tag & 7
        if wt == 0:
            v, p = read_varint(payload, p)
        elif wt == 1:
            v, p = read_fixed64(payload, p)
        elif wt == 2:
            ln, p = read_varint(payload, p)
            v = payload[p:p+ln]
            p += ln
        elif wt == 5:
            v, p = read_fixed32(payload, p)
        else:
            break
        fields[field] = v
    step = fields.get(2)
    # summary = field 5
    summary = fields.get(5)
    if summary is not None:
        # parse Summary message: repeated Value field 1
        sp = 0
        send = len(summary)
        while sp < send:
            tag, sp = read_varint(summary, sp)
            f = tag >> 3
            wt = tag & 7
            if wt == 2:
                ln, sp = read_varint(summary, sp)
                vs = summary[sp:sp+ln]
                sp += ln
                if f == 1:
                    # Value message: tag=field1 string, simple_value=field2 fixed32
                    vp = 0
                    vend = len(vs)
                    tag_name = None
                    value = None
                    while vp < vend:
                        t2, vp = read_varint(vs, vp)
                        f2 = t2 >> 3
                        wt2 = t2 & 7
                        if wt2 == 2 and f2 == 1:
                            l2, vp = read_varint(vs, vp)
                            tag_name = vs[vp:vp+l2].decode('utf-8', 'replace')
                            vp += l2
                        elif wt2 == 5 and f2 == 2:
                            value, vp = read_fixed32(vs, vp)
                        else:
                            break
                    records.append((step, tag_name, value))
            else:
                break

# print last 40 records
for r in records[-60:]:
    print(r)
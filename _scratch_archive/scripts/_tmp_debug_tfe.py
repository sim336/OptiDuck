import struct

path = r"E:\optiDuck\microduck_rl\logs\rsl_rl\velocity\2026-09-13_21-54-46_velocity\events.out.tfevents.1789307693.WenAndLiu.20380.0"

def read_varint(buf, pos):
    result = 0; shift = 0
    while True:
        b = buf[pos]; pos += 1
        result |= (b & 0x7f) << shift
        if not (b & 0x80): return result, pos
        shift += 7

data = open(path, 'rb').read()
pos = 0
events = []
while pos + 12 <= len(data):
    length = struct.unpack_from('<Q', data, pos)[0]
    pos += 8
    pos += 4
    payload = data[pos:pos+length]
    pos += length
    p = 0
    fields = {}
    while p < len(payload):
        tag, p = read_varint(payload, p)
        field = tag >> 3; wt = tag & 7
        if wt == 0:
            v, p = read_varint(payload, p)
        elif wt == 1:
            v = struct.unpack_from('<d', payload, p)[0]; p += 8
        elif wt == 2:
            ln, p = read_varint(payload, p); v = payload[p:p+ln]; p += ln
        elif wt == 5:
            v = struct.unpack_from('<f', payload, p)[0]; p += 4
        else:
            break
        fields[field] = v
    events.append(fields)

print("total events:", len(events))
print("last 5 events field keys and types:")
for e in events[-5:]:
    desc = {k: type(v).__name__ + ((' len=%d' % len(v)) if isinstance(v, bytes) else (' = %r' % (v,) if not isinstance(v, bytes) else '')) for k, v in e.items()}
    print(desc)
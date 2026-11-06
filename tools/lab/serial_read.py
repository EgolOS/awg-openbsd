import socket, sys, time
p = sys.argv[1]
out = sys.argv[2]
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.settimeout(1.0)
s.connect(p)
buf = b""
end = time.time() + float(sys.argv[3] if len(sys.argv) > 3 else 8)
while time.time() < end:
    try:
        c = s.recv(65536)
        if not c:
            break
        buf += c
    except socket.timeout:
        continue
open(out, "wb").write(buf)
print("captured", len(buf), "bytes")

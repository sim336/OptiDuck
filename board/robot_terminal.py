#!/usr/bin/env python3
"""Remote shell over WebSocket for Radxa ZERO 3W.

⚠️ DEPRECATED —— 不要部署这个文件。
   这是第一版远程终端：绑定 0.0.0.0:8091，且**没有任何鉴权**，任何人连上就是一个 login shell。
   它已被 `report_terminal.py`（端口 8071，首包必须带 token）取代，板上跑的是后者。
   保留此文件只为记录演进过程；如果你确实要用它，请先自己想清楚网络边界。

Bridges a login bash (pty) to a WebSocket on port 8091, so the Android
app / any browser ws client can type commands and see output.

Security note: this exposes a shell. It is bound 0.0.0.0 and has NO auth
by design (LAN/private mesh only). Keep the mesh private.
"""

import asyncio
import fcntl
import os
import pty
import struct
import termios

import websockets

PORT = 8091
CMD = ["/bin/bash", "--login"]


async def handle(ws):
    pid, fd = pty.fork()
    if pid == 0:
        os.environ.setdefault("TERM", "xterm-256color")
        os.environ["COLUMNS"] = "120"
        os.environ["LINES"] = "30"
        try:
            os.execvp(CMD[0], CMD)
        except Exception:
            os._exit(127)

    loop = asyncio.get_running_loop()
    closed = asyncio.Event()

    def resize(rows, cols):
        try:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
        except OSError:
            pass

    def on_pty_readable():
        # drain pty -> ws
        data = b""
        try:
            data = os.read(fd, 65536)
        except OSError:
            pass
        if not data:
            closed.set()
            loop.remove_reader(fd)
            try:
                asyncio.ensure_future(ws.close())
            except Exception:
                pass
            return
        try:
            loop.create_task(ws.send(data.decode("utf-8", "replace")))
        except Exception:
            pass

    loop.add_reader(fd, on_pty_readable)
    tx = set()  # optional pending bookkeeping

    try:
        async for raw in ws:
            if isinstance(raw, str) and raw.startswith("\x00"):
                # "resize <rows> <cols>" private message
                parts = raw.strip("\x00").split()
                if len(parts) == 3:
                    resize(int(parts[1]), int(parts[2]))
                continue
            payload = raw.encode() if isinstance(raw, str) else bytes(raw)
            if payload:
                try:
                    os.write(fd, payload)
                except OSError:
                    break
    finally:
        loop.remove_reader(fd)
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.kill(pid, 9)
        except OSError:
            pass


async def main():
    async with websockets.serve(handle, "0.0.0.0", PORT, max_size=1 << 20):
        print(f"ws shell on ws://0.0.0.0:{PORT}", flush=True)
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
#!/usr/bin/env python3
"""WebSocket interactive terminal for the Radxa board (port 8071).

Allows the BoardStatus app to open a real black-box shell into the board and
type commands. Uses a PTY so interactive programs, line editing and colors work.

Auth: the client MUST send a JSON auth frame as its very first message:
    {"type":"auth","token":"<token>"}
Token is auto-generated once and stored at /home/radxa/robot_terminal_token.

Protocol (JSON text frames from client, raw text to client):
  -> {"type":"auth","token":"..."}   * required first
  -> {"type":"resize","rows":N,"cols":N}
  -> "<raw shell input>"             any other text is sent to stdin as-is
  <- "<raw terminal output>"         streamed output / prompts / colors
"""

import asyncio
import fcntl
import os
import pty
import random
import string
import struct
import subprocess
import termios

import websockets

PORT = 8071
TOKEN_FILE = "/home/radxa/robot_terminal_token"
SHELL = ["/bin/bash", "-i"]


def load_token():
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE) as f:
            t = f.read().strip()
        if t:
            return t
    t = "".join(random.choice(string.ascii_letters + string.digits) for _ in range(16))
    tmp = TOKEN_FILE + ".tmp"
    with open(tmp, "w") as f:
        f.write(t + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, TOKEN_FILE)
    return t


async def _relay_out(ws, master, loop):
    """PTY master -> websocket, as raw text."""
    while True:
        try:
            data = await loop.run_in_executor(None, os.read, master, 4096)
        except OSError:
            break
        if not data:
            break
        try:
            await ws.send(data.decode("utf-8", "replace"))
        except Exception:
            break
    try:
        os.close(master)
    except OSError:
        pass


async def handler(ws):
    token = load_token()

    # Spawn an interactive shell on a PTY.
    master, slave = pty.openpty()
    try:
        proc = subprocess.Popen(
            SHELL,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            preexec_fn=os.setsid,
            close_fds=True,
        )
    finally:
        os.close(slave)

    master_fd = master
    loop = asyncio.get_running_loop()

    writer_task = asyncio.create_task(_relay_out(ws, master_fd, loop))

    authed = False
    try:
        async for raw in ws:
            try:
                msg = raw.decode("utf-8", "replace")
            except (AttributeError, UnicodeDecodeError):
                msg = raw if isinstance(raw, str) else str(raw)

            if not authed:
                # First message must be auth.
                if msg.strip().rstrip("\n") == token:
                    authed = True
                    try:
                        await ws.send("\r\x1b[32m@board: connected\x1b[0m\r\n")
                    except Exception:
                        pass
                else:
                    await ws.send("\r\nAccess denied.\r\n")
                    await ws.close()
                    break
                continue

            if msg.startswith('{"type":'):
                try:
                    import json
                    m = json.loads(msg)
                    if m.get("type") == "resize":
                        rows = int(m.get("rows", 24))
                        cols = int(m.get("cols", 80))
                        fcntl.ioctl(
                            master_fd, termios.TIOCSWINSZ,
                            struct.pack("HHHH", max(1, rows), max(1, cols), 0, 0),
                        )
                except Exception:
                    pass
                continue

            try:
                # Keep the trailing newline so bash readline submits the command.
                await loop.run_in_executor(None, os.write, master_fd, msg.encode())
            except Exception:
                break
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        try:
            proc.kill()
        except Exception:
            pass
        try:
            proc.wait(timeout=2)
        except Exception:
            pass
        if not writer_task.done():
            writer_task.cancel()


async def main():
    token = load_token()
    print(f"terminal listening on :{PORT} (token={token})")
    async with websockets.serve(handler, "0.0.0.0", PORT):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
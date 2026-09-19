#!/usr/bin/env python3
"""Robot status HTTP service for Radxa ZERO 3W.

Serves system health metrics as JSON over HTTP on port 8070.
Pure stdlib + psutil, no official robot daemons involved.

Endpoints:
  GET /api/status -> JSON health snapshot
  GET /health     -> "ok"
  GET /update/check -> JSON update metadata (if manifest present)
  GET /download   -> serves APK file
"""

import json
import os
import socket
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psutil

PORT = 8070

# Android 客户端据此判断是否有新版本。
# manifest: {"versionCode": N, "versionName": "x.y", "size": bytes}
UPDATE_MANIFEST = "/home/radxa/robot_update.json"


def _read_temp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            return round(int(f.read().strip()) / 1000.0, 1)
    except Exception:
        return None


def _ips():
    ips = []
    try:
        for addrs in psutil.net_if_addrs().values():
            for a in addrs:
                if a.family == socket.AF_INET:
                    ip = a.address
                    if ip and not ip.startswith("127.") and ip != "0.0.0.0":
                        ips.append(ip)
    except Exception:
        pass
    return ips


def _collect():
    vm = psutil.virtual_memory()
    du = psutil.disk_usage("/")
    try:
        load = [round(x, 2) for x in os.getloadavg()]
    except (OSError, AttributeError):
        load = None
    boot = psutil.boot_time()
    return {
        "hostname": socket.gethostname(),
        "ips": _ips(),
        "uptime_s": int(time.time() - boot),
        "cpu_percent": psutil.cpu_percent(interval=0.2),
        "cpu_count": psutil.cpu_count(logical=True),
        "mem_used": vm.used,
        "mem_total": vm.total,
        "swap_used": psutil.swap_memory().used,
        "swap_total": psutil.swap_memory().total,
        "disk_used": du.used,
        "disk_total": du.total,
        "temp": _read_temp(),
        "load": load,
        "ts": time.time(),
    }


def _term_config():
    """终端服务信息：App 据此拼 WebSocket 地址与鉴权 token。"""
    token = ""
    try:
        with open("/home/radxa/robot_terminal_token") as f:
            token = f.read().strip()
    except OSError:
        pass
    return {"port": 8071, "wss": False, "token": token}


class Handler(BaseHTTPRequestHandler):
    def _handle_update_check(self):
        try:
            with open(UPDATE_MANIFEST, "r") as f:
                meta = json.load(f)
            apk = "/home/radxa/robot_status_apk.apk"
            size = os.path.getsize(apk) if os.path.exists(apk) else meta.get("size", 0)
            out = {
                "versionCode": meta.get("versionCode", 0),
                "versionName": meta.get("versionName", ""),
                "size": size,
                "apkPath": "/download",
                "changelog": meta.get("changelog", ""),
            }
            self._send(200, json.dumps(out))
        except (OSError, ValueError):
            self._send(200, json.dumps({"versionCode": 0, "update": False}))

    def _send(self, code, body, ctype="application/json"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            self._send(200, "ok", "text/plain")
        elif self.path == "/update/check":
            self._handle_update_check()
        elif self.path == "/api/status":
            self._send(200, json.dumps(_collect()))
        elif self.path == "/api/term":
            self._send(200, json.dumps(_term_config()))
        elif self.path in ("/", "/index.html"):
            try:
                with open("/home/radxa/robot_status_index.html", "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except OSError:
                self._send(404, json.dumps({"error": "page missing"}))
        elif self.path == "/download":
            try:
                with open("/home/radxa/robot_status_apk.apk", "rb") as f:
                    self._send(200, f.read(), "application/vnd.android.package-archive")
            except OSError:
                self._send(404, json.dumps({"error": "apk missing"}))
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

    def log_message(self, fmt, *args):
        pass


def main():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
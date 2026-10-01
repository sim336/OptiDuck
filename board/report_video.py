#!/usr/bin/env python3
"""MJPEG camera service for the Radxa board (port 8072).

Lets the BoardStatus app watch the IMX219 camera in real time. Frames are
grabbed through the board's own GStreamer stack

    v4l2src -> videorate -> videoconvert -> jpegenc -> appsink

and pushed to clients as ``multipart/x-mixed-replace``. We talk to
/dev/video0 directly -- no official robot daemon (mediad / rkaiq_3A_server)
is involved.

Endpoints (everything except /health needs ``?token=<token>`` or an
``X-Token`` header; the token is the board token in TOKEN_FILE):
  GET /video     -> MJPEG stream
  GET /snapshot  -> one JPEG with the latest frame
  GET /stats     -> JSON {running, fps, seq, clients, error}
  GET /health    -> "ok"

The pipeline is started lazily on the first client and torn down after
IDLE_STOP_S with nobody watching, so an idle robot burns no CPU (measured
0.0% of one core with the pipeline stopped, ~16% while streaming one client
at 640x480@15).

Note: /dev/video0 is exclusive -- a second capture process fails with
"Device '/dev/video0' is busy", so this service cannot run alongside mediad
or any other grabber.

Note: PyGObject does not expose GstAppSink.pull_sample/try_pull_sample (the
GIR annotations hide them), so frames are collected with the ``new-sample``
signal and ``sink.emit("pull-sample")`` inside the callback.
"""

import argparse
import hmac
import json
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

PORT = 8072
DEVICE = "/dev/video0"
TOKEN_FILE = "/home/radxa/robot_terminal_token"

WIDTH = 640
HEIGHT = 480
FPS = 15
QUALITY = 80

IDLE_STOP_S = 5.0         # stop the pipeline this long after the last client leaves
FIRST_FRAME_TIMEOUT_S = 10.0
STREAM_STALL_S = 15.0     # drop a client that saw no new frame for this long
BOUNDARY = "duckframe"


def read_token():
    """Board token, re-read per request so a rotated token takes effect."""
    try:
        with open(TOKEN_FILE) as f:
            return f.read().strip()
    except OSError:
        return ""


class Camera:
    """Lazily started capture pipeline that keeps only the newest frame."""

    def __init__(self, device, width, height, fps, quality):
        self.device = device
        self.width = width
        self.height = height
        self.fps = fps
        self.quality = quality

        self._lock = threading.Lock()
        self._frame = None          # newest JPEG bytes
        self._seq = 0               # frames produced since the service started
        self._run_frames = 0        # frames produced by the current run
        self._stamps = deque(maxlen=60)
        self._clients = 0
        self._last_use = 0.0
        self._started_at = 0.0
        self._pipeline = None
        self._sink = None
        self._monitor = None
        self._error = None

    # ---------------------------------------------------------------- pipeline

    def _build(self):
        desc = (
            "v4l2src device={} ! "
            "video/x-raw,width={},height={} ! "
            "videorate ! video/x-raw,framerate={}/1 ! "
            "videoconvert ! jpegenc quality={} ! "
            "appsink name=sink max-buffers=1 drop=true sync=false"
        ).format(self.device, self.width, self.height, self.fps, self.quality)
        pipeline = Gst.parse_launch(desc)
        return pipeline, pipeline.get_by_name("sink")

    def start(self):
        with self._lock:
            if self._pipeline is not None:
                return
            self._error = None
        try:
            pipeline, sink = self._build()
        except Exception as e:                      # bad pipeline string / plugin missing
            with self._lock:
                self._error = "pipeline build failed: {}".format(e)
            return

        sink.set_property("emit-signals", True)
        sink.connect("new-sample", self._on_sample)
        pipeline.set_state(Gst.State.PLAYING)

        with self._lock:
            self._pipeline = pipeline
            self._sink = sink
            self._run_frames = 0
            self._started_at = time.time()
            self._last_use = self._started_at
            self._monitor = threading.Thread(target=self._monitor_loop, daemon=True)
            self._monitor.start()

    def stop(self):
        with self._lock:
            pipeline, self._pipeline = self._pipeline, None
            self._sink = None
            self._frame = None
            self._stamps.clear()
            self._started_at = 0.0
        if pipeline is not None:
            pipeline.set_state(Gst.State.NULL)

    def _on_sample(self, sink):
        """appsink new-sample callback: runs on the GStreamer streaming thread."""
        try:
            sample = sink.emit("pull-sample")
            if sample is None:
                return Gst.FlowReturn.OK
            buf = sample.get_buffer()
            ok, info = buf.map(Gst.MapFlags.READ)
            if not ok:
                return Gst.FlowReturn.OK
            data = bytes(info.data)
            buf.unmap(info)
            with self._lock:
                self._frame = data
                self._seq += 1
                self._run_frames += 1
                self._stamps.append(time.time())
        except Exception as e:                      # never break the pipeline
            with self._lock:
                self._error = "frame error: {}".format(e)
        return Gst.FlowReturn.OK

    def _monitor_loop(self):
        """Idle shutdown, bus errors and 'camera never came up' watchdog."""
        while True:
            with self._lock:
                pipeline = self._pipeline
                started = self._started_at
                run_frames = self._run_frames
                idle = self._clients == 0 and time.time() - self._last_use > IDLE_STOP_S
            if pipeline is None:
                return
            if idle:
                self.stop()
                return
            if run_frames == 0 and time.time() - started > FIRST_FRAME_TIMEOUT_S:
                with self._lock:
                    self._error = self._error or "no frames from {}".format(self.device)
                self.stop()
                return
            msg = pipeline.get_bus().pop_filtered(Gst.MessageType.ERROR)
            if msg is not None:
                err, _dbg = msg.parse_error()
                with self._lock:
                    self._error = err.message
                self.stop()
                return
            time.sleep(0.5)

    def _ensure_started(self):
        with self._lock:
            running = self._pipeline is not None
        if not running:
            self.start()

    # ------------------------------------------------------------------ clients

    def client_enter(self):
        with self._lock:
            self._clients += 1
            self._last_use = time.time()
        self._ensure_started()

    def client_leave(self):
        with self._lock:
            self._clients = max(0, self._clients - 1)
            self._last_use = time.time()

    def frames(self):
        """Yield JPEGs as they are produced; return when the camera dies."""
        last = -1
        last_frame_at = time.time()
        while True:
            with self._lock:
                seq, frame, error = self._seq, self._frame, self._error
                self._last_use = time.time()
            if error:
                return
            if frame is not None and seq != last:
                last = seq
                last_frame_at = time.time()
                yield frame
                continue
            if time.time() - last_frame_at > STREAM_STALL_S:
                return
            time.sleep(0.01)

    def snapshot(self, timeout=FIRST_FRAME_TIMEOUT_S):
        """Latest frame, blocking up to ``timeout``. Caller must hold a client."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if self._frame is not None:
                    return self._frame
                if self._error:
                    return None
            time.sleep(0.05)
        return None

    def stats(self):
        with self._lock:
            stamps = list(self._stamps)
            fps = 0.0
            if len(stamps) > 1:
                span = stamps[-1] - stamps[0]
                if span > 0:
                    fps = round((len(stamps) - 1) / span, 2)
            return {
                "running": self._pipeline is not None,
                "fps": fps,
                "seq": self._seq,
                "clients": self._clients,
                "width": self.width,
                "height": self.height,
                "error": self._error,
            }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    camera = None

    def _query(self):
        return parse_qs(urlparse(self.path).query)

    def _authed(self, query):
        want = read_token()
        if not want:
            return False
        got = (query.get("token") or [""])[0] or self.headers.get("X-Token", "")
        return hmac.compare_digest(got, want)

    def _json(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlparse(self.path).path
        query = self._query()

        if path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")
            return

        if not self._authed(query):
            self._json(401, {"error": "bad or missing token"})
            return

        if path == "/stats":
            self._json(200, self.camera.stats())
        elif path == "/snapshot":
            self.camera.client_enter()
            try:
                jpg = self.camera.snapshot()
            finally:
                self.camera.client_leave()
            if jpg is None:
                self._json(503, {"error": self.camera.stats()["error"] or "no frame yet"})
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(jpg)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(jpg)
        elif path == "/video":
            self._stream()
        else:
            self._json(404, {"error": "not found"})

    def _stream(self):
        cam = self.camera
        cam.client_enter()
        try:
            # Wait for the first frame before committing to a 200, so a dead
            # camera yields a real error code instead of an empty stream.
            first = cam.snapshot()
            if first is None:
                self._json(503, {"error": cam.stats()["error"] or "no frame yet"})
                return

            self.send_response(200)
            self.send_header(
                "Content-Type",
                "multipart/x-mixed-replace; boundary={}".format(BOUNDARY),
            )
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()

            def part(jpg):
                self.wfile.write(
                    "--{}\r\nContent-Type: image/jpeg\r\nContent-Length: {}\r\n\r\n".format(
                        BOUNDARY, len(jpg)
                    ).encode()
                )
                self.wfile.write(jpg)
                self.wfile.write(b"\r\n")

            part(first)
            for jpg in cam.frames():
                part(jpg)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            cam.client_leave()

    def log_message(self, fmt, *args):
        pass


def main():
    ap = argparse.ArgumentParser(description="board MJPEG camera service")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--device", default=DEVICE)
    ap.add_argument("--width", type=int, default=WIDTH)
    ap.add_argument("--height", type=int, default=HEIGHT)
    ap.add_argument("--fps", type=int, default=FPS)
    ap.add_argument("--quality", type=int, default=QUALITY)
    args = ap.parse_args()

    Gst.init(None)

    Handler.camera = Camera(args.device, args.width, args.height, args.fps, args.quality)

    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    server.daemon_threads = True
    print(
        "video listening on :{} (device={}, {}x{}@{}fps, q{})".format(
            args.port, args.device, args.width, args.height, args.fps, args.quality
        ),
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
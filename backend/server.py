"""
server.py — Local analysis server for Farseer Screener
Listens on port 8765. Start via start.py or: python server.py

Routes:
  GET /analyze?ticker=X      — kick off analyze.py X in background (skip if JSON exists)
  GET /reanalyze?ticker=X    — delete existing JSON and re-scrape
  GET /status?ticker=X       — {"ready": true/false, "running": true/false}
  GET /screener-status       — {"running": true/false}
"""

import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from config import REFRESH_INTERVAL_HOURS

PORT = int(os.environ.get("PORT", 8765))
ROOT = Path(__file__).parent
OUTPUT_DIR = ROOT.parent / "output"
TICKERS_DIR = OUTPUT_DIR / "data" / "tickers"
# In Docker: /app/frontend/  Locally: backend/../frontend/
FRONTEND_DIR = ROOT / "frontend" if (ROOT / "frontend").exists() else ROOT.parent / "frontend"

_running: dict[str, subprocess.Popen] = {}
_queued: set[str] = set()  # tickers waiting for the chromium lock
_screener_proc = None  # type: subprocess.Popen | None

# Serialise all Chromium jobs — only one browser process at a time.
_chromium_lock = threading.Lock()


def run_screener():
    global _screener_proc
    if _screener_proc is not None and _screener_proc.poll() is None:
        print("  [scheduler] Screener already running, skipping.")
        return
    def _run():
        global _screener_proc
        with _chromium_lock:
            _screener_proc = subprocess.Popen(
                [sys.executable, str(ROOT / "screener.py")],
                stdout=sys.stdout,
                stderr=sys.stderr,
            )
            print(f"  [scheduler] Started screener (pid {_screener_proc.pid})", flush=True)
            _screener_proc.wait()
            print("  [scheduler] Screener finished.", flush=True)
    threading.Thread(target=_run, daemon=True).start()


def screener_scheduler():
    if REFRESH_INTERVAL_HOURS <= 0:
        return
    interval = REFRESH_INTERVAL_HOURS * 3600
    print(f"  [scheduler] Auto-refresh every {REFRESH_INTERVAL_HOURS}h. Running initial screener...", flush=True)
    run_screener()
    while True:
        time.sleep(interval)
        print("  [scheduler] Scheduled refresh triggered.", flush=True)
        run_screener()


class Handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(204)
        self._cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        ticker = (params.get("ticker", [""])[0]).upper().strip()

        if parsed.path == "/analyze":
            self._handle_analyze(ticker, force=False)
        elif parsed.path == "/reanalyze":
            self._handle_analyze(ticker, force=True)
        elif parsed.path == "/status":
            self._handle_status(ticker)
        elif parsed.path == "/screener-status":
            self._handle_screener_status()
        elif parsed.path.startswith("/data/"):
            self._handle_file(parsed.path)
        else:
            self._handle_frontend(parsed.path)

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _handle_frontend(self, path):
        # Serve static frontend files; / and /screener.html both → screener.html
        MIME = {
            ".html": "text/html",
            ".js":   "application/javascript",
            ".css":  "text/css",
            ".png":  "image/png",
            ".ico":  "image/x-icon",
        }
        name = path.lstrip("/") or "screener.html"
        # resolve inside FRONTEND_DIR only — no path traversal
        try:
            file_path = (FRONTEND_DIR / name).resolve()
            FRONTEND_DIR.resolve()
            file_path.relative_to(FRONTEND_DIR.resolve())
        except (ValueError, Exception):
            self._json(403, {"error": "forbidden"})
            return
        if not file_path.exists():
            self._json(404, {"error": "not found"})
            return
        ext = file_path.suffix.lower()
        mime = MIME.get(ext, "application/octet-stream")
        body = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _handle_file(self, path):
        # Serve files from OUTPUT_DIR, restricted to .json under data/
        safe = path.lstrip("/")
        if not safe.startswith("data/") or not safe.endswith(".json"):
            self._json(403, {"error": "forbidden"})
            return
        file_path = OUTPUT_DIR / safe
        if not file_path.exists():
            self._json(404, {"error": "not found"})
            return
        body = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _handle_analyze(self, ticker, force=False):
        if not ticker:
            self._json(400, {"error": "missing ticker"})
            return

        # Clean up finished jobs
        if ticker in _running:
            proc = _running[ticker]
            if proc.poll() is not None:
                del _running[ticker]

        if ticker in _running or ticker in _queued:
            self._json(200, {"status": "already_running", "ticker": ticker})
            return

        if force:
            json_path = TICKERS_DIR / f"{ticker}.json"
            if json_path.exists():
                json_path.unlink()
                print(f"  [server] Deleted {ticker}.json for re-analysis")

        def _run(t):
            _queued.add(t)
            print(f"  [server] Queued analysis for {t} (waiting for Chromium lock)", flush=True)
            with _chromium_lock:
                _queued.discard(t)
                proc = subprocess.Popen(
                    [sys.executable, str(ROOT / "analyze.py"), t],
                    stdout=sys.stdout,
                    stderr=sys.stderr,
                )
                _running[t] = proc
                print(f"  [server] Started analysis for {t} (pid {proc.pid})", flush=True)
                proc.wait()
                print(f"  [server] Analysis finished for {t}.", flush=True)

        threading.Thread(target=_run, args=(ticker,), daemon=True).start()
        self._json(200, {"status": "started", "ticker": ticker})

    def _handle_screener_status(self):
        running = _screener_proc is not None and _screener_proc.poll() is None
        self._json(200, {"running": running})

    def _handle_status(self, ticker):
        if not ticker:
            self._json(400, {"error": "missing ticker"})
            return

        ready = (TICKERS_DIR / f"{ticker}.json").exists()

        running = ticker in _queued
        if not running and ticker in _running:
            proc = _running[ticker]
            if proc.poll() is None:
                running = True
            else:
                del _running[ticker]

        self._json(200, {"ticker": ticker, "ready": ready, "running": running})

    def _json(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass  # suppress default request logs


def run():
    t = threading.Thread(target=screener_scheduler, daemon=True)
    t.start()
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    server.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    print(f"  [server] Listening on port {PORT}")
    server.serve_forever()


if __name__ == "__main__":
    run()

#!/usr/bin/env python3
"""
Minimal local HTTP server for the betting page.

  python3 serve.py            # http://localhost:8765/

  GET  /                      → serves bets.html
  POST /refresh               → runs the full pipeline (--force) and re-
                                generates bets.html; returns JSON status.

The refresh button on the page calls /refresh via fetch(), then reloads.
"""

import http.server
import json
import socketserver
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
HTML = ROOT / "bets.html"
RUN_SCRIPT = ROOT / "run.py"
PORT = 8765

sys.path.insert(0, str(ROOT / "src"))


class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write(f"[serve] {self.address_string()} - {fmt % args}\n")

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if HTML.exists():
                self.wfile.write(HTML.read_bytes())
            else:
                self.wfile.write(b"<h1>bets.html non genere. Lance python3 run.py.</h1>")
            return
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True}).encode())
            return
        self.send_response(404)
        self.end_headers()

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _send_json(self, payload, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def do_POST(self):
        if self.path == "/refresh":
            try:
                result = subprocess.run(
                    [sys.executable, str(RUN_SCRIPT), "--force"],
                    capture_output=True, text=True, timeout=120, cwd=str(ROOT),
                )
                if result.returncode == 0:
                    resp = {"status": "ok", "stdout": result.stdout[-500:]}
                else:
                    resp = {"status": "error",
                            "detail": (result.stderr or result.stdout)[-500:]}
            except subprocess.TimeoutExpired:
                resp = {"status": "error", "detail": "timeout after 120s"}
            except Exception as e:
                resp = {"status": "error", "detail": str(e)}
            return self._send_json(resp)

        if self.path == "/update-results":
            try:
                from fetch_results import update_results
                summary = update_results(days_from=3)
                # Re-render the page so the perf tab reflects new results
                subprocess.run(
                    [sys.executable, str(RUN_SCRIPT)],
                    capture_output=True, text=True, timeout=60, cwd=str(ROOT),
                )
                resp = {"status": "ok", **summary}
            except Exception as e:
                resp = {"status": "error", "detail": str(e)}
            return self._send_json(resp)

        if self.path == "/mark-outcome":
            try:
                from performance import save_manual_outcome
                body = self._read_json_body()
                save_manual_outcome(body["event_id"], body["category"], body["outcome"])
                # Regenerate page so the row reflects the new outcome
                subprocess.run(
                    [sys.executable, str(RUN_SCRIPT)],
                    capture_output=True, text=True, timeout=60, cwd=str(ROOT),
                )
                resp = {"status": "ok"}
            except Exception as e:
                resp = {"status": "error", "detail": str(e)}
            return self._send_json(resp)

        self.send_response(404)
        self.end_headers()


def main():
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", PORT), Handler) as srv:
        print(f"Paris du jour — serveur local sur http://localhost:{PORT}/")
        print("Ctrl-C pour arrêter.")
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            print("\nArrêt.")


if __name__ == "__main__":
    main()

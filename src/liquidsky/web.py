"""Web dashboard: a thin Flask layer over the existing Bot.

Read endpoints (`/api/status`, `/api/report`, `/api/equity`) just read the trade
ledger — fast and safe. Action endpoints run the bot's cycle in a background
thread so the HTTP request returns immediately while the UI polls for progress.

A single lock serializes bot cycles, so a manual "once" can never overlap the
continuous loop (both touch the same files / API session). Log records from the
`liquidsky` logger are captured into a ring buffer and streamed to the UI.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from pathlib import Path
from typing import Deque, Dict, List

from flask import Flask, jsonify, request, send_from_directory

from .config import Config
from .desk import Desk

STATIC_DIR = Path(__file__).resolve().parent / "web_static"


class LogBuffer(logging.Handler):
    """In-memory ring buffer of recent log lines, exposed to the dashboard."""

    def __init__(self, capacity: int = 400):
        super().__init__()
        self._lines: Deque[dict] = deque(maxlen=capacity)
        self._seq = 0
        self.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        self._seq += 1
        self._lines.append({
            "id": self._seq,
            "level": record.levelname,
            "text": self.format(record),
        })

    def since(self, after_id: int) -> List[dict]:
        return [ln for ln in self._lines if ln["id"] > after_id]


class Runner:
    """Owns the bot and the background-thread lifecycle for once/run."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.desk = Desk(cfg)
        self._lock = threading.Lock()      # serializes cycles
        self._busy = False                 # a single "once" cycle is running
        self._loop = False                 # continuous loop active
        self._loop_thread: threading.Thread | None = None
        self.log = logging.getLogger("liquidsky")

    @property
    def state(self) -> Dict:
        return {
            "env": self.cfg.env,
            "busy": self._busy,
            "looping": self._loop,
            "scan_interval_minutes": self.cfg.scan_interval_minutes,
        }

    def _run_cycle(self) -> None:
        with self._lock:
            self._busy = True
            try:
                self.desk.run_once()
            except Exception:
                self.log.exception("Cycle failed")
            finally:
                self._busy = False

    def run_once(self) -> bool:
        """Start one cycle in the background. Returns False if one is in flight."""
        if self._busy or self._loop:
            return False
        threading.Thread(target=self._run_cycle, daemon=True).start()
        return True

    def start_loop(self) -> bool:
        if self._loop:
            return False
        self._loop = True

        def _loop():
            interval = self.cfg.scan_interval_minutes * 60
            self.log.info("Dashboard loop started [env=%s] every %d min",
                          self.cfg.env, self.cfg.scan_interval_minutes)
            while self._loop:
                self._run_cycle()
                # Sleep in short slices so Stop is responsive.
                for _ in range(int(interval)):
                    if not self._loop:
                        break
                    time.sleep(1)
            self.log.info("Dashboard loop stopped")

        self._loop_thread = threading.Thread(target=_loop, daemon=True)
        self._loop_thread.start()
        return True

    def stop_loop(self) -> None:
        self._loop = False


def create_app(cfg: Config) -> Flask:
    app = Flask(__name__, static_folder=None)
    runner = Runner(cfg)

    log_buffer = LogBuffer()
    root_logger = logging.getLogger("liquidsky")
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(log_buffer)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    # ----------------------------------------------------------- static
    @app.get("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    @app.get("/<path:filename>")
    def static_files(filename):
        return send_from_directory(STATIC_DIR, filename)

    # -------------------------------------------------------- read APIs
    @app.get("/api/strategies")
    def api_strategies():
        return jsonify({"strategies": runner.desk.list_strategies(),
                        "default": runner.desk.default_key})

    @app.get("/api/status")
    def api_status():
        key = request.args.get("strategy")
        return jsonify({**runner.desk.status_data(key), "runner": runner.state})

    @app.get("/api/report")
    def api_report():
        key = request.args.get("strategy")
        return jsonify(runner.desk.report_data(key))

    @app.get("/api/equity")
    def api_equity():
        key = request.args.get("strategy")
        return jsonify({"points": runner.desk.equity_series(key)})

    @app.get("/api/compare")
    def api_compare():
        return jsonify(runner.desk.compare())

    @app.get("/api/logs")
    def api_logs():
        after = int(request.args.get("after", 0))
        return jsonify({"lines": log_buffer.since(after)})

    # ------------------------------------------------------ action APIs
    @app.post("/api/run-once")
    def api_run_once():
        ok = runner.run_once()
        return jsonify({"started": ok, "runner": runner.state}), (200 if ok else 409)

    @app.post("/api/loop/start")
    def api_loop_start():
        ok = runner.start_loop()
        return jsonify({"started": ok, "runner": runner.state}), (200 if ok else 409)

    @app.post("/api/loop/stop")
    def api_loop_stop():
        runner.stop_loop()
        return jsonify({"runner": runner.state})

    return app


def serve(cfg: Config, host: str = "127.0.0.1", port: int = 8787) -> None:
    app = create_app(cfg)
    # threaded=True so background cycles + polling requests run concurrently.
    app.run(host=host, port=port, threaded=True, use_reloader=False)

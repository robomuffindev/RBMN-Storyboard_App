#!/usr/bin/env python3
"""
RBMN Storyboard App — Desktop launcher
Starts the FastAPI backend and opens the React frontend in a pywebview native window.
"""

import argparse
import logging
import os
import signal
import sys
import threading
import time

import uvicorn


def start_backend(host: str, port: int, log_level: str):
    """Start the FastAPI backend server in a thread."""
    uvicorn.run(
        "backend.main:app",
        host=host,
        port=port,
        log_level=log_level.lower(),
        reload=False,
        timeout_keep_alive=300,  # 5 min keep-alive for long-running requests (Demucs)
    )


def start_desktop(host: str, port: int, title: str = "Robomuffin Idea Factory"):
    """Open the app in a native pywebview window."""
    try:
        import webview

        # Wait for backend to be ready
        import requests

        url = f"http://{host}:{port}"
        for _ in range(30):
            try:
                r = requests.get(f"{url}/api/health", timeout=1)
                if r.status_code == 200:
                    break
            except Exception:
                time.sleep(0.5)

        window = webview.create_window(
            title,
            url=url,
            width=1600,
            height=900,
            min_size=(1200, 700),
            text_select=True,
        )
        webview.start(debug=False)

    except ImportError:
        logging.warning(
            "pywebview not installed. Opening in browser instead. "
            "Install with: pip install pywebview"
        )
        import webbrowser
        import requests

        url = f"http://{host}:{port}"
        for _ in range(60):
            try:
                r = requests.get(f"{url}/api/health", timeout=1)
                if r.status_code == 200:
                    break
            except Exception:
                time.sleep(0.5)

        webbrowser.open(url)
        # Keep main thread alive
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass


def clear_pycache():
    """Remove __pycache__ dirs so Python loads fresh .py files."""
    import shutil
    from pathlib import Path
    root = Path(__file__).parent / "backend"
    count = 0
    for cache_dir in root.rglob("__pycache__"):
        shutil.rmtree(cache_dir, ignore_errors=True)
        count += 1
    if count:
        logging.getLogger("rbmn").info(f"Cleared {count} __pycache__ directories")


def _read_network_access_setting() -> bool:
    """Read network_access from SQLite DB (sync, runs once at startup)."""
    import sqlite3
    from pathlib import Path
    db_path = Path("~/RBMN-Projects/RBMN.db").expanduser()
    if not db_path.exists():
        return False
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.execute("SELECT network_access FROM app_settings WHERE id = 1")
        row = cursor.fetchone()
        conn.close()
        return bool(row[0]) if row else False
    except Exception:
        return False


def main():
    clear_pycache()
    parser = argparse.ArgumentParser(description="Robomuffin Idea Factory")
    parser.add_argument("--host", default=None, help="Backend host (default: auto from settings)")
    parser.add_argument("--port", type=int, default=8899, help="Backend port (default: 8899)")
    parser.add_argument(
        "--mode",
        choices=["desktop", "browser", "server"],
        default="desktop",
        help="Run mode: desktop (pywebview), browser (opens browser), server (API only)",
    )
    parser.add_argument(
        "--log-level", default="INFO", help="Log level (default: INFO)"
    )
    args = parser.parse_args()

    # Resolve host: CLI flag > DB setting > default localhost
    if args.host is None:
        if _read_network_access_setting():
            args.host = "0.0.0.0"
        else:
            args.host = "127.0.0.1"

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger = logging.getLogger("rbmn")
    logger.info(f"Starting Robomuffin Idea Factory on {args.host}:{args.port} (mode: {args.mode})")

    if args.mode == "server":
        # Just run the backend, no UI
        start_backend(args.host, args.port, args.log_level)
    else:
        # Start backend in a background thread
        backend_thread = threading.Thread(
            target=start_backend,
            args=(args.host, args.port, args.log_level),
            daemon=True,
        )
        backend_thread.start()

        if args.mode == "desktop":
            start_desktop(args.host, args.port)
        else:
            # Browser mode — wait for backend to be ready before opening
            import webbrowser
            import requests

            url = f"http://{args.host}:{args.port}"
            logger.info("Waiting for backend to be ready...")
            for i in range(60):
                try:
                    r = requests.get(f"{url}/api/health", timeout=1)
                    if r.status_code == 200:
                        logger.info("Backend is ready.")
                        break
                except Exception:
                    pass
                time.sleep(0.5)

            webbrowser.open(url)
            logger.info("Opened browser. Press Ctrl+C to stop.")
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                logger.info("Shutting down...")


if __name__ == "__main__":
    main()

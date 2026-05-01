"""
start.py — Farseer Screener launcher
Starts the local server, runs the screener, opens the browser.

Usage:
    python start.py
"""

import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).parent
OUTPUT_DIR = ROOT.parent / "output"


def main():
    print("\n=== Farseer Screener ===\n")

    # Sync analysis.html to output so it's always up to date
    src = ROOT.parent / "frontend" / "analysis.html"
    dst = OUTPUT_DIR / "analysis.html"
    shutil.copy2(src, dst)
    print("  analysis.html synced to output/")

    # Start the local server in the background
    server_proc = subprocess.Popen(
        [sys.executable, str(ROOT / "server.py")],
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    time.sleep(1)
    print()

    try:
        # Run the screener (this takes a while)
        subprocess.run([sys.executable, str(ROOT / "screener.py")], check=True)

        # Open the result in the browser
        screener_html = OUTPUT_DIR / "screener.html"
        print(f"\n  Opening {screener_html}...")
        webbrowser.open(f"file://{screener_html.resolve()}")

    finally:
        # Keep server alive so card clicks keep working
        print("\n  Server is running — press Ctrl+C to stop.\n")
        try:
            server_proc.wait()
        except KeyboardInterrupt:
            server_proc.terminate()
            print("\n  Stopped.")


if __name__ == "__main__":
    main()

"""Boot the studio the newcomer way: python studio/run.py (or start.bat).

Puts studio/ on sys.path, starts uvicorn on 127.0.0.1:$STUDIO_PORT (8765
default, D6: localhost only), opens the browser after a short delay, and —
when a dependency is missing — prints exactly what to pip install (via the
doctor's import check) instead of a traceback.
"""

import os
import sys
import threading
import webbrowser
from pathlib import Path

STUDIO_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(STUDIO_DIR))

PORT = int(os.environ.get("STUDIO_PORT", "8765"))
URL = f"http://127.0.0.1:{PORT}"


def _import_failed(exc: ImportError) -> None:
    print("Campaign Studio cannot start yet: "
          f"missing or broken dependency ({exc.name or exc})")
    print()
    try:
        import doctor
        check = doctor.check_imports()
        if check["detail"]:
            print(f"  {check['detail']}")
        if check.get("fix"):
            print(f"  fix: {check['fix']}")
    except Exception:  # noqa: BLE001 — the doctor itself should never crash the boot story
        print("  (the doctor could not run to say more)")
    print()
    print(f"Run:  python -m pip install -r {STUDIO_DIR / 'requirements.txt'}")
    print("Then: python studio/doctor.py  for a full check-up.")
    sys.exit(1)


try:
    import uvicorn  # noqa: E402
    import server   # noqa: E402
except ImportError as e:  # noqa: PERF203 — this is the friendly-boot path
    _import_failed(e)


def main() -> None:
    print()
    print("  Campaign Studio")
    print(f"  {URL}")
    print("  Ctrl+C to stop")
    print()

    opener = threading.Timer(1.5, webbrowser.open, args=(URL,))
    opener.daemon = True
    opener.start()

    try:
        uvicorn.run(server.app, host="127.0.0.1", port=PORT, log_level="info")
    except KeyboardInterrupt:
        pass
    print("Studio stopped.")


if __name__ == "__main__":
    main()

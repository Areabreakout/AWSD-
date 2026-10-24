# -*- coding: utf-8 -*-
"""
WASD+ Clone launcher (pure Python)
- Checks that all dependencies are importable
- Auto-installs any missing package (falls back to Tsinghua mirror)
- Then runs main.py
Run via:  py launcher.py   or   python launcher.py
"""
import sys
import os
import subprocess
import importlib

# Force unbuffered, UTF-8 stdout so logs are visible immediately
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass


def log(msg):
    print(msg, flush=True)


# Dependencies: (import_name, pip_name)
DEPS = [
    ("numpy", "numpy"),
    ("PyQt5", "PyQt5"),
    ("pynput", "pynput"),
    ("cv2", "opencv-python"),
    ("av", "av"),
]


def is_importable(name):
    try:
        importlib.import_module(name)
        return True
    except Exception:
        return False


def pip_install(packages):
    """Install packages, retrying with Tsinghua mirror on failure."""
    cmd = [sys.executable, "-m", "pip", "install"] + packages
    log("[launcher] installing: " + " ".join(packages))
    r = subprocess.call(cmd)
    if r != 0:
        log("[launcher] retrying with Tsinghua mirror...")
        r = subprocess.call(cmd + ["-i", "https://pypi.tuna.tsinghua.edu.cn/simple"])
    return r


def main():
    log("=" * 44)
    log("  AWSD+++ Launcher")
    log("  Python: " + sys.version.split()[0] + " -> " + sys.executable)
    log("=" * 44)

    missing = [pip for imp, pip in DEPS if not is_importable(imp)]
    if missing:
        log("[launcher] missing: " + ", ".join(missing))
        pip_install(missing)
    else:
        log("[launcher] all dependencies OK")

    # Re-check after install
    still_missing = [pip for imp, pip in DEPS if not is_importable(imp)]
    if still_missing:
        log("[launcher] WARNING these are still unavailable: " + ", ".join(still_missing))
        log("[launcher] the program may fail to start.")

    log("-" * 44)
    log("[launcher] starting AWSD+++...")
    log("-" * 44)

    # Run main.py as a subprocess so the console shows its output
    here = os.path.dirname(os.path.abspath(__file__))
    main_py = os.path.join(here, "main.py")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return subprocess.call([sys.executable, "-u", main_py], env=env)


if __name__ == "__main__":
    try:
        code = main()
    except Exception as e:
        log("[launcher] FATAL: " + repr(e))
        code = 1
    if code != 0:
        log("[launcher] exited with code " + str(code))
        try:
            input("Press Enter to close...")
        except Exception:
            pass
    sys.exit(code)

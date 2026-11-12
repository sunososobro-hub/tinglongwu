"""Nuitka entry script for tlwu.exe (see packaging/build_windows.py)."""
import sys

from tlwu_app.main import main

if __name__ == "__main__":
    sys.exit(main())

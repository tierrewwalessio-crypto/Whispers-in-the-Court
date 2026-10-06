"""Entry point of WhispersInTheCourt.exe (see tools/build_exe.py).

Double-clicked, it opens the Court Brain window: it installs or updates the
mod, then follows the game. The command-line options of `python -m
courtbrain` work here too.
"""

from courtbrain.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())

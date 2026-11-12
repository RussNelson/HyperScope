#!/usr/bin/env python3
"""HyperScope desktop application.

From this directory:

    python3 hyperscope.py
    python3 hyperscope.py path/to/file.opml

With no file, the tutorial in data/demos is opened.
"""

from hs.app import main

if __name__ == "__main__":
    raise SystemExit(main())

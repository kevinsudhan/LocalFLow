"""LocalFlow - privacy-first local voice dictation for Windows."""
from __future__ import annotations

import os

# Hugging Face's Xet transfer backend has been observed to stall at zero bytes
# on some Windows networks, which looks to the user like a hung model download.
# The classic HTTP path is marginally slower but reliable. Set
# LOCALFLOW_ENABLE_XET=1 to opt back in.
if not os.environ.get("LOCALFLOW_ENABLE_XET"):
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

__version__ = "1.0.0"

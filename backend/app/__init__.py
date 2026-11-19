"""VeriHire backend (FastAPI). Owner: Aditya Raj.

``docint`` (the ML package) is normally installed with ``pip install -e ml``. If it is not,
fall back to the repo's ml/ folder so ``uvicorn app.main:app --app-dir backend --loop app.eventloop:selector_loop`` always starts.
"""

import importlib.util as _ilu
import sys as _sys
from pathlib import Path as _Path

if _ilu.find_spec("docint") is None:
    _ml = _Path(__file__).resolve().parents[2] / "ml"
    if (_ml / "docint").is_dir():
        _sys.path.insert(0, str(_ml))

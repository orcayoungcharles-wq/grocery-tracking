"""Shim so callers can import from aldi-parser.py (hyphenated filename).

Exports both the original parseAldi API (unit tests) and parse_receipt
(app.py / test_pipeline.py).
"""
import importlib.util
import sys
from pathlib import Path

_path = Path(__file__).resolve().parent / "aldi-parser.py"
_spec = importlib.util.spec_from_file_location("aldi_parser", _path)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["aldi_parser"] = _mod  # required so @dataclass can resolve its module
_spec.loader.exec_module(_mod)

parseAldi = _mod.parseAldi
parse_receipt = _mod.parseAldi  # alias used by app / pipeline
ALDI_8_17 = _mod.ALDI_8_17
ALDI_9_1 = _mod.ALDI_9_1
AldiItem = _mod.AldiItem
AldiReceipt = _mod.AldiReceipt

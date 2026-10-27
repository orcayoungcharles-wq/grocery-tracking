"""Shim so callers can import from kroger-parser.py (hyphenated filename).

Exports both the original parseKroger API (unit tests) and parse_receipt
(app.py / test_pipeline.py).
"""
import importlib.util
import sys
from pathlib import Path

_path = Path(__file__).resolve().parent / "kroger-parser.py"
_spec = importlib.util.spec_from_file_location("kroger_parser", _path)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["kroger_parser"] = _mod
_spec.loader.exec_module(_mod)

parseKroger = _mod.parseKroger
parse_receipt = _mod.parseKroger  # alias used by app / pipeline
text_823 = _mod.text_823
text_826 = _mod.text_826
text_826b = _mod.text_826b
text_914 = _mod.text_914

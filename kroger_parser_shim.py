import importlib.util, os, sys
spec = importlib.util.spec_from_file_location("kroger_parser", os.path.join(os.path.dirname(__file__), "kroger-parser.py"))
mod = importlib.util.module_from_spec(spec)
sys.modules["kroger_parser"] = mod   # <-- required so @dataclass can resolve its module
spec.loader.exec_module(mod)
parse_receipt = mod.parseKroger
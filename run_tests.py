"""Dependency-free test runner for environments without pytest.
Normal use:  python -m pytest"""
import contextlib, importlib, sys, traceback, types
sys.path.insert(0, "src")
try:
    import pytest  # noqa: F401
except ImportError:                       # tiny stand-in: only pytest.raises is used
    @contextlib.contextmanager
    def _raises(exc):
        try:
            yield
        except exc:
            return
        raise AssertionError(f"{exc.__name__} not raised")
    sys.modules["pytest"] = types.SimpleNamespace(raises=_raises)
fails = 0
for name in ("tests.test_routers", "tests.test_framework", "tests.test_edit", "tests.test_tool", "tests.test_editor"):
    mod = importlib.import_module(name)
    for n in sorted(dir(mod)):
        if n.startswith("test_"):
            try:
                getattr(mod, n)(); print("PASS", n)
            except Exception:
                fails += 1; print("FAIL", n); traceback.print_exc()
sys.exit(fails)

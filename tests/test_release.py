import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "next_version", Path(__file__).resolve().parent.parent / ".github/scripts/next_version.py"
)
nv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nv)


def test_bump_types():
    assert nv.bump_for("fix: crash") == "patch"
    assert nv.bump_for("perf(api): faster") == "patch"
    assert nv.bump_for("feat: new thing") == "minor"
    assert nv.bump_for("feat!: drop old option") == "major"
    assert nv.bump_for("fix: x", "BREAKING CHANGE: renamed") == "major"
    assert nv.bump_for("docs: readme") is None
    assert nv.bump_for("ci: workflow") is None
    assert nv.bump_for("Add something") is None


def test_next_version():
    assert nv.next_version("0.0.0", ["minor"]) == "0.1.0"
    assert nv.next_version("0.1.0", ["patch"]) == "0.1.1"
    assert nv.next_version("0.1.1", ["patch", "minor", None]) == "0.2.0"
    assert nv.next_version("0.2.0", ["major"]) == "0.3.0"  # breaking stays minor below 1.0
    assert nv.next_version("1.2.3", ["major", "patch"]) == "2.0.0"
    assert nv.next_version("1.2.3", [None, None]) is None

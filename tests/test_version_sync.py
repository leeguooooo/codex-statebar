import pathlib
import re


ROOT = pathlib.Path(__file__).parents[1]


def test_pyproject_and_source_fallback_versions_match():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    init = (ROOT / "src/codex_statebar/__init__.py").read_text(encoding="utf-8")
    version = re.search(r'^version = "([^"]+)"', pyproject, re.MULTILINE).group(1)
    fallback = re.search(r'v = "([^"]+)"', init).group(1)
    assert version == fallback

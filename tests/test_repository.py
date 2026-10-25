"""Repository-, Build- und Versionskonventionen ohne GitHub-Zugang."""
from pathlib import Path
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib
from packaging.version import Version
from mao_cli import __version__

ROOT = Path(__file__).resolve().parents[1]


def test_single_release_version():
    metadata = tomllib.loads((ROOT/'pyproject.toml').read_text())
    assert Version(metadata['project']['version']) == Version(__version__)


def test_release_and_ci_scaffolding():
    ci = (ROOT/'.github/workflows/ci.yml').read_text()
    rel = (ROOT/'.github/workflows/release.yml').read_text()
    assert 'pytest' in ci and 'develop' in ci
    assert 'sha256sum' in rel and 'gh release create' in rel
    assert 'merge-base --is-ancestor' in rel


def test_gitignore_protects_local_credentials():
    ignored = (ROOT/'.gitignore').read_text()
    assert '**/db.config' in ignored
    assert 'workspace/' in ignored


def test_wiki_and_changelog_in_main_repo():
    assert (ROOT/'CHANGELOG.md').exists()
    assert (ROOT/'wiki/Git-Flow.md').exists()
    assert (ROOT/'wiki/Updates.md').exists()

"""tinglongwu.paths (#1664): the central app root / data dir."""
from tinglongwu import paths


def test_app_root_is_repo_root_in_source_mode():
    assert (paths.APP_ROOT / "pyproject.toml").is_file()
    assert (paths.APP_ROOT / "src" / "core" / "tinglongwu" / "__init__.py").is_file()


def test_data_dir_exists():
    assert paths.DATA_DIR.is_dir()
    assert (paths.DATA_DIR / "selftest_en.wav").is_file()

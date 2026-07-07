"""Tests for PyPI vs Sisyphus version comparison."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from alt_maint_tools import alt_vs_pypi


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("1.2.3-alt1", "1.2.3"),
        ("2.0.0-alt1.2", "2.0.0"),
        ("1.0.0", "1.0.0"),
    ],
)
def test_strip_alt_tail(version: str, expected: str) -> None:
    assert alt_vs_pypi.strip_alt_tail(version) == expected


@pytest.mark.parametrize(
    ("alt_name", "pypi_name"),
    [
        ("python3-module-requests", "requests"),
        ("python3-httpx", "httpx"),
        ("python-legacy", "legacy"),
        ("vim", None),
    ],
)
def test_alt_name_to_pypi_name(alt_name: str, pypi_name: str | None) -> None:
    assert alt_vs_pypi.alt_name_to_pypi_name(alt_name) == pypi_name


def test_get_status() -> None:
    assert alt_vs_pypi.get_status("1.2.3-alt1", "1.2.3") == "Совпадает"
    assert alt_vs_pypi.get_status("1.2.3-alt1", "1.2.4") == "Обновить"
    assert alt_vs_pypi.get_status("1.2.3-alt1", "Нет на PyPI") == "Нет на PyPI"
    assert alt_vs_pypi.get_status("1.2.3-alt1", "Ошибка парсинга") == "Ошибка PyPI"


def test_get_alt_python_packages_filters_non_python() -> None:
    session = MagicMock()
    session.get.return_value.json.return_value = {
        "packages": [
            {"name": "python3-module-requests", "version": "2.31.0", "release": "alt1"},
            {"name": "vim", "version": "9.0", "release": "alt1"},
        ]
    }
    session.get.return_value.raise_for_status = MagicMock()

    packages = alt_vs_pypi.get_alt_python_packages("zerospirit", session=session)

    assert packages == [("requests", "python3-module-requests", "2.31.0-alt1")]


def test_collect_results_sorts_by_status() -> None:
    session = MagicMock()

    def fake_get(url: str, *args, **kwargs):
        response = MagicMock()
        response.raise_for_status = MagicMock()
        if "maintainer_packages" in url:
            response.json.return_value = {
                "packages": [
                    {"name": "python3-module-foo", "version": "1.0", "release": "alt1"},
                    {"name": "python3-module-bar", "version": "2.0", "release": "alt1"},
                ]
            }
        elif url.endswith("/foo/json"):
            response.ok = True
            response.json.return_value = {"info": {"version": "1.0"}}
        elif url.endswith("/bar/json"):
            response.ok = True
            response.json.return_value = {"info": {"version": "3.0"}}
        else:
            pytest.fail(f"Unexpected URL: {url}")
        return response

    session.get.side_effect = fake_get

    rows = alt_vs_pypi.collect_results("zerospirit", session=session)

    assert [row.pypi_name for row in rows] == ["bar", "foo"]
    assert rows[0].status == "Обновить"
    assert rows[1].status == "Совпадает"


def test_main_help_exits_cleanly(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        alt_vs_pypi.main(["-h"])
    captured = capsys.readouterr()
    assert exc.value.code == 0
    assert "maintainer" in captured.out
    assert "--version" in captured.out

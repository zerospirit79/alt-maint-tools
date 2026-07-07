"""Tests for branch version comparison."""

from __future__ import annotations

from unittest.mock import MagicMock

from alt_maint_tools import branch_compare


def test_compare_versions() -> None:
    assert branch_compare.compare_versions("1.0-alt1", "1.0-alt1") == "Совпадает"
    assert branch_compare.compare_versions("1.0-alt1", "2.0-alt1") == "Различается"
    assert branch_compare.compare_versions("1.0-alt1", None) == "Отсутствует"


def test_collect_results() -> None:
    session = MagicMock()

    def fake_get(url: str, *args, **kwargs):
        response = MagicMock()
        response.raise_for_status = MagicMock()
        if "maintainer_packages" in url and "branch=sisyphus" in url:
            response.json.return_value = {
                "packages": [
                    {"name": "foo", "version": "1.0", "release": "alt1"},
                    {"name": "bar", "version": "2.0", "release": "alt1"},
                ]
            }
        elif "repository_packages?branch=p11" in url:
            response.json.return_value = {
                "packages": [
                    {"name": "foo", "version": "1.0", "release": "alt1"},
                ]
            }
        else:
            raise AssertionError(f"Unexpected URL: {url}")
        return response

    session.get.side_effect = fake_get

    rows = branch_compare.collect_results("zerospirit", "p11", session=session)

    assert len(rows) == 2
    foo = next(row for row in rows if row.name == "foo")
    bar = next(row for row in rows if row.name == "bar")
    assert foo.status == "Совпадает"
    assert bar.status == "Отсутствует"
    assert bar.target_version == "-"


def test_format_table_uses_branch_name() -> None:
    rows = [
        branch_compare.BranchRow(
            name="pkg",
            sisyphus_version="1.0-alt1",
            target_version="1.0-alt1",
            status="Совпадает",
        )
    ]
    table = branch_compare.format_table(rows, "c10f2")
    assert "C10F2 version" in table

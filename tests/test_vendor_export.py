"""Tests for vendor export helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from alt_maint_tools import vendor_export


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        (["go.mod"], "go"),
        (["Cargo.toml"], "rust"),
        (["Gemfile"], "ruby"),
        (["package.json"], "node"),
        ([], None),
    ],
)
def test_detect_project_type(tmp_path: Path, files: list[str], expected: str | None) -> None:
    for name in files:
        (tmp_path / name).write_text("", encoding="utf-8")
    assert vendor_export.detect_project_type(tmp_path) == expected


def test_export_vendors_unknown_project(tmp_path: Path) -> None:
    with pytest.raises(vendor_export.VendorExportError, match="Не удалось определить тип"):
        vendor_export.export_vendors(tmp_path)


def test_vendor_go(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module example.com/demo\n", encoding="utf-8")

    with patch.object(vendor_export.shutil, "which", return_value="/usr/bin/go"):
        with patch.object(vendor_export, "run_command") as run_command:
            vendor_export.vendor_go(tmp_path)

    assert [call.args[0] for call in run_command.call_args_list] == [
        ["go", "mod", "tidy"],
        ["go", "mod", "vendor"],
    ]


def test_vendor_rust_modern(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text("[package]\nname = \"demo\"\n", encoding="utf-8")

    def fake_run_command(args: list[str], *, cwd: Path) -> None:
        if args == ["cargo", "vendor", "vendor"]:
            vendor_dir = cwd / "vendor"
            vendor_dir.mkdir()
            (vendor_dir / "crate").mkdir()

    with patch.object(vendor_export.shutil, "which", return_value="/usr/bin/cargo"):
        with patch.object(vendor_export, "run_command", side_effect=fake_run_command) as run_command:
            vendor_export.vendor_rust(tmp_path)

    run_command.assert_called_once_with(["cargo", "vendor", "vendor"], cwd=tmp_path)


def test_vendor_rust_legacy(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text("[package]\nname = \"demo\"\n", encoding="utf-8")
    legacy_src = tmp_path / "target" / "vendor" / "src" / "demo-crate"
    legacy_src.mkdir(parents=True)
    (legacy_src / "lib.rs").write_text("// demo\n", encoding="utf-8")

    with patch.object(vendor_export.shutil, "which", return_value="/usr/bin/cargo"):
        with patch.object(vendor_export, "run_command", side_effect=vendor_export.VendorExportError("fail")):
            vendor_export.vendor_rust(tmp_path)

    assert (tmp_path / "vendor" / "demo-crate" / "lib.rs").is_file()


def test_read_package_name_fallback(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    assert vendor_export._read_package_name(tmp_path) == tmp_path.name


def test_main_help_exits_cleanly(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        vendor_export.main(["-h"])
    captured = capsys.readouterr()
    assert exc.value.code == 0
    assert "project_dir" in captured.out
    assert "--version" in captured.out

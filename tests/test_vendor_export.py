"""Tests for vendor export helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

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


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        (["bun.lock"], "bun"),
        (["bun.lockb"], "bun"),
        (["pnpm-lock.yaml"], "pnpm"),
        (["pnpm-workspace.yaml"], "pnpm"),
        (["yarn.lock"], "yarn"),
        (["package-lock.json"], "npm"),
        ([], "npm"),
    ],
)
def test_detect_node_package_manager(
    tmp_path: Path, files: list[str], expected: str
) -> None:
    for name in files:
        (tmp_path / name).write_text("", encoding="utf-8")
    assert vendor_export.detect_node_package_manager(tmp_path) == expected


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


def test_read_package_name_scoped(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"name": "@scope/my-pkg"}',
        encoding="utf-8",
    )
    assert vendor_export._read_package_name(tmp_path) == "scope-my-pkg"


def test_unignore_node_modules_in_gitignore(tmp_path: Path) -> None:
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text(
        "\n".join(
            [
                "# Logs",
                "logs",
                "**/node_modules/**",
                "_node_modules",
                "node_modules",
                "node_modules_*",
                "src/node-fallbacks/node_modules",
                "dist/",
                "",
            ]
        ),
        encoding="utf-8",
    )

    changed = vendor_export.unignore_node_modules_in_gitignore(tmp_path)
    text = gitignore.read_text(encoding="utf-8")

    assert changed == 5
    assert "# alt-vendor-export: **/node_modules/**" in text
    assert "# alt-vendor-export: _node_modules" in text
    assert "# alt-vendor-export: node_modules" in text
    assert "# alt-vendor-export: node_modules_*" in text
    assert "# alt-vendor-export: src/node-fallbacks/node_modules" in text
    assert "dist/" in text
    assert text.splitlines()[0] == "# Logs"


def test_strip_native_binaries(tmp_path: Path) -> None:
    modules = tmp_path / "node_modules" / "native"
    modules.mkdir(parents=True)
    elf = modules / "addon.node"
    elf.write_bytes(b"\x7fELF" + b"\0" * 20)
    js_bin = modules / "cli.js"
    js_bin.write_text("#!/usr/bin/env node\nconsole.log(1)\n", encoding="utf-8")
    js_bin.chmod(0o755)

    removed = vendor_export._strip_native_binaries(tmp_path / "node_modules")
    assert removed == 1
    assert not elf.exists()
    assert js_bin.is_file()


def test_vendor_node_policy_layout(tmp_path: Path) -> None:
    """Default export matches node-mocha: .gear/predownloaded-production/node_modules."""
    (tmp_path / "package.json").write_text(
        '{"name": "mocha", "dependencies": {"ms": "2.0.0"}}',
        encoding="utf-8",
    )
    (tmp_path / ".gitignore").write_text("node_modules/\n", encoding="utf-8")

    def fake_run_command(args: list[str], *, cwd: Path, env=None) -> None:
        modules = cwd / "node_modules" / "ms"
        modules.mkdir(parents=True)
        (modules / "package.json").write_text('{"name":"ms"}', encoding="utf-8")
        # ELF must be stripped from production tree.
        (modules / "native.node").write_bytes(b"\x7fELF\0\0")

    with patch.object(vendor_export.shutil, "which", return_value="/usr/bin/npm"):
        with patch.object(vendor_export, "run_command", side_effect=fake_run_command):
            with patch.object(vendor_export, "_remove_dev_packages"):
                with patch.object(vendor_export, "_deduplicate_system_node_modules"):
                    vendor_export.vendor_node(tmp_path)

    prod = tmp_path / ".gear" / "predownloaded-production" / "node_modules" / "ms"
    dev = tmp_path / ".gear" / "predownloaded-development" / "node_modules" / "ms"
    assert (prod / "package.json").is_file()
    assert (dev / "package.json").is_file()
    assert not (prod / "native.node").exists()
    # Policy default: no in-tree node_modules, gitignore untouched.
    assert not (tmp_path / "node_modules").exists()
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8") == "node_modules/\n"
    # No package-name nesting (unlike older vendor.sh).
    assert not (tmp_path / ".gear" / "predownloaded-production" / "mocha").exists()


def test_vendor_node_inplace(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"name": "demo", "dependencies": {"left-pad": "1.0.0"}}',
        encoding="utf-8",
    )
    (tmp_path / ".gitignore").write_text("node_modules/\n", encoding="utf-8")

    def fake_run_command(args: list[str], *, cwd: Path, env=None) -> None:
        modules = cwd / "node_modules" / "left-pad"
        modules.mkdir(parents=True)
        (modules / "index.js").write_text("1", encoding="utf-8")

    with patch.object(vendor_export.shutil, "which", return_value="/usr/bin/npm"):
        with patch.object(vendor_export, "run_command", side_effect=fake_run_command):
            with patch.object(vendor_export, "_remove_dev_packages"):
                with patch.object(vendor_export, "_deduplicate_system_node_modules"):
                    vendor_export.vendor_node(tmp_path, inplace=True)

    assert (tmp_path / "node_modules" / "left-pad" / "index.js").is_file()
    assert "# alt-vendor-export: node_modules/" in (tmp_path / ".gitignore").read_text(
        encoding="utf-8"
    )


def test_vendor_node_pnpm_workspace(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"name": "monorepo-root", "private": true}',
        encoding="utf-8",
    )
    (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")
    (tmp_path / "pnpm-workspace.yaml").write_text("packages:\n  - 'packages/*'\n", encoding="utf-8")

    def fake_run_command(args: list[str], *, cwd: Path, env=None) -> None:
        assert args[:2] == ["pnpm", "install"]
        (cwd / "node_modules" / "demo").mkdir(parents=True)
        (cwd / "node_modules" / "demo" / "index.js").write_text("1", encoding="utf-8")

    with patch.object(vendor_export.shutil, "which", return_value="/usr/bin/pnpm"):
        with patch.object(vendor_export, "run_command", side_effect=fake_run_command) as run_command:
            vendor_export.vendor_node(tmp_path)

    run_command.assert_called_once()
    assert (
        tmp_path / ".gear" / "predownloaded-production" / "node_modules" / "demo" / "index.js"
    ).is_file()
    # Without --inplace temporary workspace install is cleaned up.
    assert not (tmp_path / "node_modules").exists()


def test_vendor_node_bun_inplace(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"name": "bun", "workspaces": ["./packages/*"]}',
        encoding="utf-8",
    )
    (tmp_path / "bun.lock").write_text("{}\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("node_modules\n", encoding="utf-8")

    def fake_run_command(args: list[str], *, cwd: Path, env=None) -> None:
        assert args == ["bun", "install"]
        (cwd / "node_modules" / "esbuild").mkdir(parents=True)

    with patch.object(vendor_export.shutil, "which", return_value="/usr/bin/bun"):
        with patch.object(vendor_export, "run_command", side_effect=fake_run_command):
            vendor_export.vendor_node(tmp_path, inplace=True)

    assert (tmp_path / "node_modules" / "esbuild").is_dir()
    assert "# alt-vendor-export: node_modules" in (tmp_path / ".gitignore").read_text(
        encoding="utf-8"
    )


def test_main_help_exits_cleanly(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        vendor_export.main(["-h"])
    captured = capsys.readouterr()
    assert exc.value.code == 0
    assert "project_dir" in captured.out
    assert "--inplace" in captured.out
    assert "--version" in captured.out

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


def test_detect_project_type_pnpm_monorepo_with_cargo(tmp_path: Path) -> None:
    """pnpm keeps Cargo.toml at the repo root but is packaged as Node.js."""
    (tmp_path / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    (tmp_path / "pnpm-workspace.yaml").write_text("packages:\n  - '*'\n", encoding="utf-8")
    (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: 9\n", encoding="utf-8")
    assert vendor_export.detect_project_type(tmp_path) == "node"


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

    def fake_run_command(args: list[str], *, cwd: Path, env=None) -> None:
        if args == ["go", "mod", "vendor"]:
            vendor = cwd / "vendor" / "example.com" / "demo"
            vendor.mkdir(parents=True)
            (vendor / "mod.go").write_text("package demo\n", encoding="utf-8")

    with patch.object(vendor_export.shutil, "which", return_value="/usr/bin/go"):
        with patch.object(vendor_export, "run_command", side_effect=fake_run_command) as run_command:
            vendor_export.vendor_go(tmp_path)

    assert [call.args[0] for call in run_command.call_args_list] == [
        ["go", "mod", "tidy"],
        ["go", "mod", "vendor"],
    ]
    assert (
        tmp_path / "vendor" / "example.com" / "demo" / "mod.go"
    ).is_file()
    assert not (tmp_path / ".gear" / "predownloaded-production").exists()


def test_vendor_go_inplace(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module example.com/demo\n", encoding="utf-8")

    def fake_run_command(args: list[str], *, cwd: Path, env=None) -> None:
        if args == ["go", "mod", "vendor"]:
            (cwd / "vendor" / "pkg").mkdir(parents=True)

    with patch.object(vendor_export.shutil, "which", return_value="/usr/bin/go"):
        with patch.object(vendor_export, "run_command", side_effect=fake_run_command):
            vendor_export.vendor_go(tmp_path, inplace=True)

    assert (tmp_path / "vendor" / "pkg").is_dir()
    assert not (tmp_path / ".gear" / "predownloaded-production").exists()


def test_vendor_rust_modern(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text("[package]\nname = \"demo\"\n", encoding="utf-8")

    def fake_run_cargo(project_dir: Path, args: list[str]) -> str:
        assert args == ["cargo", "vendor", "vendor"]
        vendor_dir = project_dir / "vendor"
        vendor_dir.mkdir()
        (vendor_dir / "crate").mkdir()
        return '[source.vendored-sources]\ndirectory = "vendor"\n'

    with patch.object(vendor_export.shutil, "which", return_value="/usr/bin/cargo"):
        with patch.object(vendor_export, "_run_cargo_vendor", side_effect=fake_run_cargo):
            vendor_export.vendor_rust(tmp_path)

    assert (tmp_path / "vendor" / "crate").is_dir()
    assert "vendored-sources" in (tmp_path / ".gear" / "config.toml").read_text(encoding="utf-8")
    assert not (tmp_path / ".gear" / "predownloaded-production").exists()


def test_vendor_rust_legacy(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text("[package]\nname = \"demo\"\n", encoding="utf-8")
    legacy_src = tmp_path / "target" / "vendor" / "src" / "demo-crate"
    legacy_src.mkdir(parents=True)
    (legacy_src / "lib.rs").write_text("// demo\n", encoding="utf-8")

    with patch.object(vendor_export.shutil, "which", return_value="/usr/bin/cargo"):
        with patch.object(
            vendor_export,
            "_run_cargo_vendor",
            side_effect=vendor_export.VendorExportError("fail"),
        ):
            vendor_export.vendor_rust(tmp_path)

    assert (tmp_path / "vendor" / "demo-crate" / "lib.rs").is_file()
    assert not (tmp_path / ".gear" / "predownloaded-production").exists()


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


def test_remove_all_node_modules(tmp_path: Path) -> None:
    root_modules = tmp_path / "node_modules" / "left-pad"
    root_modules.mkdir(parents=True)
    (root_modules / "index.js").write_text("1", encoding="utf-8")
    nested = tmp_path / ".meta-updater" / "node_modules" / "write-json-file"
    nested.mkdir(parents=True)
    (nested / "index.js").write_text("2", encoding="utf-8")
    (tmp_path / ".gear" / "predownloaded-production" / "node_modules" / "keep").mkdir(
        parents=True
    )
    (tmp_path / ".git" / "node_modules" / "ignored").mkdir(parents=True)

    vendor_export._remove_all_node_modules(tmp_path)

    assert not (tmp_path / "node_modules").exists()
    assert not (tmp_path / ".meta-updater" / "node_modules").exists()
    # Gear / .git trees must not be swept.
    assert (tmp_path / ".gear" / "predownloaded-production" / "node_modules" / "keep").is_dir()
    assert (tmp_path / ".git" / "node_modules" / "ignored").is_dir()


def test_vendor_node_pnpm_workspace(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"name": "monorepo-root", "private": true}',
        encoding="utf-8",
    )
    (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")
    (tmp_path / "pnpm-workspace.yaml").write_text("packages:\n  - 'packages/*'\n", encoding="utf-8")
    stale = tmp_path / ".meta-updater" / "node_modules" / "write-json-file"
    stale.mkdir(parents=True)
    (stale / "index.js").write_text("stale", encoding="utf-8")

    def fake_run_command(args: list[str], *, cwd: Path, env=None) -> None:
        assert args[:2] == ["pnpm", "install"]
        assert not (cwd / ".meta-updater" / "node_modules").exists()
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


@pytest.mark.parametrize(
    ("files", "package_json", "cli_major", "expected"),
    [
        ([".yarnrc.yml"], "{}", None, "berry"),
        ([], '{"packageManager": "yarn@4.2.1+sha256.abc"}', None, "berry"),
        ([], '{"packageManager": "yarn@1.22.22"}', None, "classic"),
        ([], "{}", 1, "classic"),
        ([], "{}", 4, "berry"),
        ([], "{}", None, "classic"),
    ],
)
def test_detect_yarn_generation(
    tmp_path: Path,
    files: list[str],
    package_json: str,
    cli_major: int | None,
    expected: str,
) -> None:
    (tmp_path / "package.json").write_text(package_json, encoding="utf-8")
    for name in files:
        (tmp_path / name).write_text("", encoding="utf-8")

    with patch.object(vendor_export, "_yarn_major_from_cli", return_value=cli_major):
        assert vendor_export.detect_yarn_generation(tmp_path) == expected


def test_node_install_command_yarn_berry() -> None:
    """Yarn 2+ has no --frozen-lockfile/--ignore-scripts/--production flags."""
    assert vendor_export._node_install_command(
        "yarn", production=False, generation="berry"
    ) == ["yarn", "install", "--immutable"]
    assert vendor_export._node_install_command(
        "yarn", production=True, generation="berry"
    ) == ["yarn", "workspaces", "focus", "--production"]
    # Yarn 1 keeps its own flags.
    assert vendor_export._node_install_command("yarn", production=False) == [
        "yarn",
        "install",
        "--frozen-lockfile",
        "--ignore-scripts",
    ]


def test_yarn_berry_install_env() -> None:
    assert vendor_export._yarn_install_env("classic") is None
    berry_env = vendor_export._yarn_install_env("berry")
    assert berry_env is not None
    # The pinned yarn must be used (yarnPath): any other one rewrites the
    # lockfile and --immutable fails.
    assert "YARN_IGNORE_PATH" not in berry_env
    assert berry_env["YARN_ENABLE_SCRIPTS"] == "false"
    assert berry_env["YARN_NODE_LINKER"] == "node-modules"
    # berry projects guard committed .pnp.* with immutablePatterns; the
    # node-modules linker drops those files, so the guard has to go.
    assert berry_env["YARN_IMMUTABLE_PATTERNS"] == "[]"


def test_preserve_yarn_pnp_artifacts_restores_committed_files(tmp_path: Path) -> None:
    (tmp_path / ".pnp.cjs").write_text("pnp", encoding="utf-8")
    (tmp_path / ".pnp.loader.mjs").write_text("loader", encoding="utf-8")

    with vendor_export._preserve_yarn_pnp_artifacts(tmp_path, "berry"):
        (tmp_path / ".pnp.cjs").unlink()
        (tmp_path / ".pnp.data.json").write_text("{}", encoding="utf-8")

    assert (tmp_path / ".pnp.cjs").read_text(encoding="utf-8") == "pnp"
    assert (tmp_path / ".pnp.loader.mjs").read_text(encoding="utf-8") == "loader"
    # Artifacts the install invented are not left behind either.
    assert not (tmp_path / ".pnp.data.json").exists()


def test_preserve_yarn_pnp_artifacts_restores_on_failure(tmp_path: Path) -> None:
    (tmp_path / ".pnp.cjs").write_text("pnp", encoding="utf-8")

    with pytest.raises(RuntimeError):
        with vendor_export._preserve_yarn_pnp_artifacts(tmp_path, "berry"):
            (tmp_path / ".pnp.cjs").unlink()
            raise RuntimeError("install failed")

    assert (tmp_path / ".pnp.cjs").read_text(encoding="utf-8") == "pnp"


def test_prepare_node_workdir_copies_berry_config(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"name": "demo"}', encoding="utf-8")
    (tmp_path / "yarn.lock").write_text("# yarn lockfile v1\n", encoding="utf-8")
    (tmp_path / ".yarnrc.yml").write_text(
        "nodeLinker: pnp\nyarnPath: scripts/run-yarn.js\ncatalog:\n  typescript: ^5.9.2\n",
        encoding="utf-8",
    )
    patches = tmp_path / ".yarn" / "patches"
    patches.mkdir(parents=True)
    (patches / "got.patch").write_text("patch", encoding="utf-8")
    cache = tmp_path / ".yarn" / "cache"
    cache.mkdir()
    (cache / "big.zip").write_text("x", encoding="utf-8")

    work_dir = tmp_path / "work"
    work_dir.mkdir()
    vendor_export._prepare_node_workdir(tmp_path, work_dir, generation="berry")

    config = (work_dir / ".yarnrc.yml").read_text(encoding="utf-8")
    # yarnPath would point at sources that are not in the workdir.
    assert "yarnPath" not in config
    assert "catalog:" in config
    assert (work_dir / "yarn.lock").is_file()
    assert (work_dir / ".yarn" / "patches" / "got.patch").is_file()
    # The in-tree cache is not copied (it is huge and re-fetchable).
    assert not (work_dir / ".yarn" / "cache").exists()


def test_vendor_node_yarn_berry_workspace(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"name": "monorepo-root", "private": true, "workspaces": ["packages/*"]}',
        encoding="utf-8",
    )
    (tmp_path / "yarn.lock").write_text("__metadata:\n  version: 10\n", encoding="utf-8")
    (tmp_path / ".yarnrc.yml").write_text("enableGlobalCache: false\n", encoding="utf-8")

    def fake_run_command(args: list[str], *, cwd: Path, env=None) -> None:
        assert args == ["yarn", "install", "--immutable"]
        assert env is not None
        assert env["YARN_ENABLE_SCRIPTS"] == "false"
        assert env["YARN_NODE_LINKER"] == "node-modules"
        (cwd / "node_modules" / "demo").mkdir(parents=True)
        (cwd / "node_modules" / "demo" / "index.js").write_text("1", encoding="utf-8")

    with patch.object(vendor_export.shutil, "which", return_value="/usr/bin/yarn"):
        with patch.object(vendor_export, "run_command", side_effect=fake_run_command) as run_command:
            vendor_export.vendor_node(tmp_path)

    run_command.assert_called_once()
    assert (
        tmp_path / ".gear" / "predownloaded-production" / "node_modules" / "demo" / "index.js"
    ).is_file()


def test_help_cross_references_the_other_node_tool(capsys: pytest.CaptureFixture[str]) -> None:
    """-h of both Node tools must answer "which one do I need?"."""
    from alt_maint_tools import node_store

    for module, other_tool in (
        (vendor_export, "alt-node-store"),
        (node_store, "alt-vendor-export"),
    ):
        with pytest.raises(SystemExit) as exc:
            module.main(["-h"])
        captured = capsys.readouterr().out
        assert exc.value.code == 0
        assert other_tool in captured
        assert "pnpm build" in captured


def test_main_help_exits_cleanly(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        vendor_export.main(["-h"])
    captured = capsys.readouterr()
    assert exc.value.code == 0
    assert "project_dir" in captured.out
    assert "--inplace" in captured.out
    assert "--version" in captured.out

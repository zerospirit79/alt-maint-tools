"""Tests for the pnpm store dump command."""

from __future__ import annotations

import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest

from alt_maint_tools import node_store


def test_require_pnpm_project_missing(tmp_path: Path) -> None:
    with pytest.raises(node_store.NodeStoreError, match="pnpm-lock.yaml"):
        node_store.require_pnpm_project(tmp_path)


def test_require_pnpm_project_ok(tmp_path: Path) -> None:
    (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")
    node_store.require_pnpm_project(tmp_path)


def test_supported_architectures_block() -> None:
    block = node_store.supported_architectures_block(["x64", "arm64", "ia32"], "glibc")
    assert "supportedArchitectures:" in block
    assert "    - linux" in block
    assert "    - x64" in block
    assert "    - arm64" in block
    assert "    - ia32" in block
    assert "    - glibc" in block


def test_append_supported_architectures_creates_file(tmp_path: Path) -> None:
    workspace = tmp_path / "pnpm-workspace.yaml"
    node_store.append_supported_architectures(workspace, ["x64"], "glibc")
    content = workspace.read_text(encoding="utf-8")
    assert "supportedArchitectures:" in content
    assert node_store.has_supported_architectures(workspace)


def test_append_supported_architectures_preserves_existing(tmp_path: Path) -> None:
    workspace = tmp_path / "pnpm-workspace.yaml"
    workspace.write_text("packages:\n  - 'js/*'\n", encoding="utf-8")
    node_store.append_supported_architectures(workspace, ["x64", "arm64"], "glibc")
    content = workspace.read_text(encoding="utf-8")
    assert content.startswith("packages:\n  - 'js/*'\n")
    assert "supportedArchitectures:" in content


def test_has_supported_architectures(tmp_path: Path) -> None:
    workspace = tmp_path / "pnpm-workspace.yaml"
    assert not node_store.has_supported_architectures(workspace)
    workspace.write_text(
        "supportedArchitectures:\n  os: [linux]\n", encoding="utf-8"
    )
    assert node_store.has_supported_architectures(workspace)


def test_strip_package_manager(tmp_path: Path) -> None:
    package_json = tmp_path / "package.json"
    package_json.write_text(
        '{\n  "name": "demo",\n  "packageManager": "pnpm@10.17.0"\n}\n',
        encoding="utf-8",
    )
    original = node_store.strip_package_manager(package_json)
    assert original is not None
    assert "packageManager" not in package_json.read_text(encoding="utf-8")


def test_strip_package_manager_noop(tmp_path: Path) -> None:
    package_json = tmp_path / "package.json"
    package_json.write_text('{"name": "demo"}\n', encoding="utf-8")
    assert node_store.strip_package_manager(package_json) is None


def test_strip_package_manager_missing_file(tmp_path: Path) -> None:
    assert node_store.strip_package_manager(tmp_path / "package.json") is None


def test_create_store_tar_excludes_runtime(tmp_path: Path) -> None:
    store = tmp_path / "store"
    (store / "files" / "ab").mkdir(parents=True)
    (store / "files" / "ab" / "cafe").write_text("data", encoding="utf-8")
    (store / "index.db").write_bytes(b"\0" * 8)
    (store / "links").mkdir()
    (store / "links" / "stale").write_text("x", encoding="utf-8")
    (store / ".tmp").mkdir()
    (store / ".pnpm-needs-build-marker").write_text("", encoding="utf-8")

    output = tmp_path / "pnpm-store.tar"
    count = node_store.create_store_tar(store, output)

    assert count == 1
    names = set(tarfile.open(output).getnames())
    assert "files/ab/cafe" in names
    assert "index.db" in names
    assert not any("links" in name for name in names)
    assert not any(".tmp" in name for name in names)
    assert not any("needs-build" in name for name in names)


def test_export_pnpm_store_end_to_end(tmp_path: Path) -> None:
    (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")
    (tmp_path / "package.json").write_text(
        '{\n  "name": "demo",\n  "packageManager": "pnpm@10.17.0"\n}\n',
        encoding="utf-8",
    )
    store = tmp_path / ".pnpm-store" / "v11"
    (store / "files" / "ab").mkdir(parents=True)
    (store / "files" / "ab" / "cafe").write_text("data", encoding="utf-8")
    (store / "index.db").write_bytes(b"\0" * 8)
    (store / "links").mkdir()

    def fake_capture(cmd: list[str], *, cwd: Path, env=None) -> str:
        if cmd == ["pnpm", "--version"]:
            return "11.5.0\n"
        if cmd == ["pnpm", "store", "path"]:
            return str(store) + "\n"
        if cmd[:2] == ["pnpm", "install"]:
            assert "--frozen-lockfile" in cmd
            assert "--ignore-scripts" in cmd
            assert env is not None and env.get("CI") == "true"
            return ""
        raise AssertionError(f"unexpected command: {cmd}")

    with patch.object(node_store.shutil, "which", return_value="/usr/bin/pnpm"):
        with patch.object(node_store, "_run_capture", side_effect=fake_capture):
            target = node_store.export_pnpm_store(
                tmp_path,
                cpu=["x64", "arm64", "ia32"],
                libc="glibc",
                output=Path(".gear/pnpm-store.tar"),
            )

    assert target == tmp_path / ".gear" / "pnpm-store.tar"
    assert target.is_file()
    # package.json restored with packageManager intact.
    assert "packageManager" in (tmp_path / "package.json").read_text(encoding="utf-8")
    # workspace.yaml created transiently and removed again.
    assert not (tmp_path / "pnpm-workspace.yaml").exists()


def test_main_help_exits_cleanly(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        node_store.main(["-h"])
    captured = capsys.readouterr()
    assert exc.value.code == 0
    assert "project_dir" in captured.out
    assert "--cpu" in captured.out
    assert "--output" in captured.out
    assert "--version" in captured.out

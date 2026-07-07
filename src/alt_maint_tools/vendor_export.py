"""Vendor third-party dependencies for Go, Rust, Ruby, and Node.js projects."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Literal

ProjectType = Literal["go", "rust", "ruby", "node"]


class VendorExportError(Exception):
    """Raised when vendor export cannot be completed."""


def detect_project_type(project_dir: Path) -> ProjectType | None:
    """Detect project type from marker files."""
    if (project_dir / "go.mod").is_file():
        return "go"
    if (project_dir / "Cargo.toml").is_file():
        return "rust"
    if (project_dir / "Gemfile").is_file():
        return "ruby"
    if (project_dir / "package.json").is_file():
        return "node"
    return None


def require_command(command: str, install_hint: str) -> None:
    """Ensure an external command is available on PATH."""
    if shutil.which(command) is None:
        raise VendorExportError(f"{command} не установлен. {install_hint}")


def run_command(
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
) -> None:
    """Run a subprocess and raise VendorExportError on failure."""
    try:
        subprocess.run(
            args,
            cwd=cwd,
            env=env,
            check=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        command = " ".join(args)
        raise VendorExportError(f"Команда завершилась с ошибкой: {command}") from exc


def vendor_go(project_dir: Path) -> None:
    require_command("go", "Установите golang для Go-проектов.")
    run_command(["go", "mod", "tidy"], cwd=project_dir)
    run_command(["go", "mod", "vendor"], cwd=project_dir)


def _copy_legacy_rust_vendor(project_dir: Path, legacy_vendor: Path) -> None:
    vendor_dir = project_dir / "vendor"
    vendor_dir.mkdir(parents=True, exist_ok=True)
    for subdir in ("src", "deps"):
        source = legacy_vendor / subdir
        if source.is_dir():
            for item in source.iterdir():
                destination = vendor_dir / item.name
                if destination.exists():
                    if destination.is_dir():
                        shutil.rmtree(destination)
                    else:
                        destination.unlink()
                if item.is_dir():
                    shutil.copytree(item, destination)
                else:
                    shutil.copy2(item, destination)


def vendor_rust(project_dir: Path) -> None:
    require_command("cargo", "Установите rust для Rust-проектов.")

    vendor_dir = project_dir / "vendor"
    if vendor_dir.exists():
        shutil.rmtree(vendor_dir)

    # Modern cargo (Sisyphus) vendors directly into the given directory.
    try:
        run_command(["cargo", "vendor", "vendor"], cwd=project_dir)
        if any(vendor_dir.iterdir()):
            return
    except VendorExportError:
        pass

    # Legacy cargo-vendor (p11) stores artifacts under target/vendor/.
    legacy_vendor = project_dir / "target" / "vendor"
    if legacy_vendor.is_dir():
        _copy_legacy_rust_vendor(project_dir, legacy_vendor)
        if any(vendor_dir.iterdir()):
            return

    # Fallback for setups where `cargo vendor` writes only config output.
    run_command(["cargo", "vendor"], cwd=project_dir)
    if legacy_vendor.is_dir():
        _copy_legacy_rust_vendor(project_dir, legacy_vendor)

    if not vendor_dir.exists() or not any(vendor_dir.iterdir()):
        raise VendorExportError(
            "Не удалось выгрузить Rust-вендоры. "
            "В p11 может потребоваться пакет cargo-vendor."
        )


def vendor_ruby(project_dir: Path) -> None:
    require_command("ruby", "Установите ruby для Ruby-проектов.")
    require_command(
        "bundle",
        "Установите bundler (gem install bundler) для Ruby-проектов.",
    )
    run_command(["bundle", "config", "set", "--local", "path", "vendor/bundle"], cwd=project_dir)
    run_command(["bundle", "install"], cwd=project_dir)


def _read_package_name(project_dir: Path) -> str:
    package_json = project_dir / "package.json"
    try:
        data = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return project_dir.name
    name = data.get("name")
    return name if isinstance(name, str) and name else project_dir.name


def _remove_dev_packages(work_dir: Path) -> None:
    subprocess.run(
        [
            "npm",
            "remove",
            "typescript",
            "eslint",
            "tslint",
            "tsutils",
            "node-pre-gyp",
            "--save",
        ],
        cwd=work_dir,
        check=False,
    )


def _deduplicate_system_node_modules(work_dir: Path) -> None:
    node_modules = work_dir / "node_modules"
    if not node_modules.is_dir():
        return

    node_path = os.environ.get("NODE_PATH", "/usr/lib/node_modules")
    for entry in node_modules.iterdir():
        if not entry.is_dir():
            continue
        if (Path(node_path) / entry.name).is_dir():
            shutil.rmtree(entry)

    bin_dir = node_modules / ".bin"
    if bin_dir.is_dir():
        for entry in bin_dir.iterdir():
            if not entry.is_symlink() and not os.access(entry, os.X_OK):
                entry.unlink(missing_ok=True)


def _prepare_node_workdir(project_dir: Path, work_dir: Path) -> None:
    shutil.copy2(project_dir / "package.json", work_dir / "package.json")
    lock_file = project_dir / "package-lock.json"
    if lock_file.is_file():
        shutil.copy2(lock_file, work_dir / "package-lock.json")


def vendor_node(project_dir: Path) -> None:
    require_command("npm", "Установите npm для Node.js-проектов.")

    package_name = _read_package_name(project_dir)
    gear_dir = project_dir / ".gear"
    dev_target = gear_dir / "predownloaded-development" / package_name
    prod_target = gear_dir / "predownloaded-production" / package_name
    dev_work = gear_dir / ".tmp-node-dev"
    prod_work = gear_dir / ".tmp-node-prod"

    for path in (dev_target, prod_target, dev_work, prod_work):
        if path.exists():
            shutil.rmtree(path)

    dev_target.parent.mkdir(parents=True, exist_ok=True)
    prod_target.parent.mkdir(parents=True, exist_ok=True)
    dev_work.mkdir(parents=True, exist_ok=True)
    prod_work.mkdir(parents=True, exist_ok=True)

    _prepare_node_workdir(project_dir, dev_work)
    run_command(["npm", "install"], cwd=dev_work)
    try:
        _remove_dev_packages(dev_work)
    except VendorExportError:
        pass
    shutil.copytree(dev_work / "node_modules", dev_target / "node_modules")

    _prepare_node_workdir(project_dir, prod_work)
    run_command(["npm", "install", "--omit=dev"], cwd=prod_work)
    _deduplicate_system_node_modules(prod_work)
    shutil.copytree(prod_work / "node_modules", prod_target / "node_modules")

    shutil.rmtree(dev_work)
    shutil.rmtree(prod_work)


def export_vendors(project_dir: Path) -> ProjectType:
    """Export vendors for the detected project type."""
    if not project_dir.is_dir():
        raise VendorExportError(f"Папка проекта не найдена: {project_dir}")

    project_type = detect_project_type(project_dir)
    if project_type is None:
        raise VendorExportError(
            "Не удалось определить тип проекта. "
            "Проверьте наличие go.mod, Cargo.toml, Gemfile или package.json."
        )

    exporters = {
        "go": vendor_go,
        "rust": vendor_rust,
        "ruby": vendor_ruby,
        "node": vendor_node,
    }
    exporters[project_type](project_dir)
    return project_type


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print("Использование: alt-vendor-export <путь_к_проекту>")
        return 1

    project_dir = Path(args[0]).resolve()
    try:
        project_type = export_vendors(project_dir)
    except VendorExportError as exc:
        print(exc, file=sys.stderr)
        return 1

    labels = {
        "go": "Go",
        "rust": "Rust",
        "ruby": "Ruby",
        "node": "Node.js",
    }
    print(f"Вендоры для {labels[project_type]} успешно выгружены!")
    print("Выгрузка вендоров завершена!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

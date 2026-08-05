"""Vendor third-party dependencies for Go, Rust, Ruby, and Node.js projects."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Literal

from alt_maint_tools import __version__

ProjectType = Literal["go", "rust", "ruby", "node"]
NodePackageManager = Literal["npm", "pnpm", "yarn", "bun"]

# Lines in .gitignore that would exclude node_modules from gear/hasher source trees.
_NODE_MODULES_GITIGNORE_RE = re.compile(
    r"""
    ^\s*
    !?                                   # optional negation
    (?:
        \*\*/node_modules(?:/\*\*)?      # **/node_modules or **/node_modules/**
        | (?:.*/)?node_modules(?:_[*]|\*?|/|\*\*)?  # node_modules, node_modules/, node_modules_*
        | _node_modules                  # pnpm alternate name
    )
    \s*(?:\#.*)?$
    """,
    re.VERBOSE | re.IGNORECASE,
)


class VendorExportError(Exception):
    """Raised when vendor export cannot be completed."""


def _has_node_ecosystem_markers(project_dir: Path) -> bool:
    """Return True when the tree looks like an npm/pnpm/yarn/bun project root."""
    markers = (
        "package-lock.json",
        "npm-shrinkwrap.json",
        "pnpm-lock.yaml",
        "pnpm-workspace.yaml",
        "yarn.lock",
        "bun.lock",
        "bun.lockb",
    )
    return any((project_dir / name).is_file() for name in markers)


def detect_project_type(project_dir: Path) -> ProjectType | None:
    """Detect project type from marker files."""
    if (project_dir / "go.mod").is_file():
        return "go"
    has_package_json = (project_dir / "package.json").is_file()
    # Monorepos like pnpm ship a root Cargo.toml for native crates but are
    # packaged as Node.js programs (node_modules in predownloaded-*).
    if has_package_json and _has_node_ecosystem_markers(project_dir):
        return "node"
    if (project_dir / "Cargo.toml").is_file():
        return "rust"
    if (project_dir / "Gemfile").is_file():
        return "ruby"
    if has_package_json:
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


def _strip_shared_binaries(tree: Path) -> int:
    """Drop ``.a`` / ``.so`` / ``.dll`` from vendored trees (rpmgs for non-cargo)."""
    if not tree.is_dir():
        return 0
    removed = 0
    for path in tree.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        if path.suffix.lower() in {".a", ".so", ".dll"}:
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def vendor_go(project_dir: Path, *, inplace: bool = False) -> None:
    """Vendor Go modules into ``vendor/`` in the project tree."""
    require_command("go", "Установите golang для Go-проектов.")
    env = {**os.environ, "GOWORK": "off"}
    run_command(["go", "mod", "tidy"], cwd=project_dir, env=env)
    run_command(["go", "mod", "vendor"], cwd=project_dir, env=env)
    vendor_dir = project_dir / "vendor"
    if not vendor_dir.is_dir():
        raise VendorExportError(
            f"После go mod vendor не найден каталог vendor в {project_dir}"
        )
    _strip_shared_binaries(vendor_dir)


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


def _run_cargo_vendor(project_dir: Path, args: list[str]) -> str:
    """Run cargo vendor and return stdout (source-replace config snippet)."""
    try:
        completed = subprocess.run(
            args,
            cwd=project_dir,
            check=True,
            text=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        message = f"Команда завершилась с ошибкой: {' '.join(args)}"
        if detail:
            message = f"{message}\n{detail}"
        raise VendorExportError(message) from exc
    return completed.stdout or ""


def _save_cargo_config(project_dir: Path, config_text: str) -> None:
    if not config_text.strip():
        return
    gear_dir = project_dir / ".gear"
    gear_dir.mkdir(parents=True, exist_ok=True)
    (gear_dir / "config.toml").write_text(config_text, encoding="utf-8")


def vendor_rust(project_dir: Path, *, inplace: bool = False) -> None:
    """Vendor crates into ``vendor/`` in the project tree (ALT gear / ripgrep layout).

    ``cargo vendor`` creates ``vendor/`` at the project root; source-replace config
    is saved to ``.gear/config.toml``. Unlike Node.js, Rust deps are not placed
    under ``.gear/predownloaded-*`` — gear rules use ``tar: vendor name=vendor``.
    """
    require_command("cargo", "Установите rust для Rust-проектов.")

    vendor_dir = project_dir / "vendor"
    if vendor_dir.exists():
        shutil.rmtree(vendor_dir)

    config_text = ""
    # Modern cargo (Sisyphus) vendors directly into the given directory.
    try:
        config_text = _run_cargo_vendor(project_dir, ["cargo", "vendor", "vendor"])
    except VendorExportError:
        config_text = ""

    if not vendor_dir.is_dir() or not any(vendor_dir.iterdir()):
        # Legacy cargo-vendor (p11) stores artifacts under target/vendor/.
        legacy_vendor = project_dir / "target" / "vendor"
        if legacy_vendor.is_dir():
            _copy_legacy_rust_vendor(project_dir, legacy_vendor)
        else:
            config_text = _run_cargo_vendor(project_dir, ["cargo", "vendor"])
            if legacy_vendor.is_dir():
                _copy_legacy_rust_vendor(project_dir, legacy_vendor)

    if not vendor_dir.exists() or not any(vendor_dir.iterdir()):
        raise VendorExportError(
            "Не удалось выгрузить Rust-вендоры. "
            "В p11 может потребоваться пакет cargo-vendor."
        )

    # Drop unused Windows static libs like rpmgs (cargo integrity for the rest).
    for pattern in (
        "winapi-*-pc-windows-gnu/lib/*.a",
        "winapi-*-pc-windows-gnu/lib/*.lib",
        "windows*/lib/*.a",
        "windows*/lib/*.lib",
    ):
        for path in vendor_dir.glob(pattern):
            if path.is_file():
                path.unlink(missing_ok=True)

    _save_cargo_config(project_dir, config_text)


def vendor_ruby(project_dir: Path, *, inplace: bool = False) -> None:
    """Vendor gems into ``vendor/bundle`` in the project tree (Bundler path)."""
    require_command("ruby", "Установите ruby для Ruby-проектов.")
    require_command(
        "bundle",
        "Установите bundler (gem install bundler) для Ruby-проектов.",
    )
    run_command(
        ["bundle", "config", "set", "--local", "path", "vendor/bundle"],
        cwd=project_dir,
    )
    run_command(["bundle", "install"], cwd=project_dir)
    vendor_dir = project_dir / "vendor"
    if not vendor_dir.is_dir():
        raise VendorExportError(
            f"После bundle install не найден каталог vendor в {project_dir}"
        )


def _read_package_name(project_dir: Path) -> str:
    package_json = project_dir / "package.json"
    try:
        data = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return project_dir.name
    name = data.get("name")
    if isinstance(name, str) and name:
        # Scoped names like @scope/pkg must be safe as a single path segment.
        return name.lstrip("@").replace("/", "-")
    return project_dir.name


def detect_node_package_manager(project_dir: Path) -> NodePackageManager:
    """Detect Node package manager from lockfiles / workspace markers."""
    if (project_dir / "bun.lockb").is_file() or (project_dir / "bun.lock").is_file():
        return "bun"
    if (project_dir / "pnpm-lock.yaml").is_file() or (
        project_dir / "pnpm-workspace.yaml"
    ).is_file():
        return "pnpm"
    if (project_dir / "yarn.lock").is_file():
        return "yarn"
    return "npm"


def _is_node_workspace(project_dir: Path) -> bool:
    """Return True when install must run in the project tree (monorepo)."""
    if (project_dir / "pnpm-workspace.yaml").is_file():
        return True
    if (project_dir / "pnpm-lock.yaml").is_file():
        # pnpm lockfiles often reference the full workspace layout.
        return True
    if (project_dir / "bun.lockb").is_file() or (project_dir / "bun.lock").is_file():
        return True
    package_json = project_dir / "package.json"
    if not package_json.is_file():
        return False
    try:
        data = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    workspaces = data.get("workspaces")
    return bool(workspaces)


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
    """Drop deps already packaged in %nodejs_sitelib (ALT Node.js Policy)."""
    node_modules = work_dir / "node_modules"
    if not node_modules.is_dir():
        return

    # %nodejs_sitelib from rpm-macros-nodejs is %{_prefix}/lib/node_modules
    node_path = Path(os.environ.get("NODE_PATH", "/usr/lib/node_modules"))
    for entry in list(node_modules.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        if (node_path / entry.name).is_dir():
            shutil.rmtree(entry)

    bin_dir = node_modules / ".bin"
    if bin_dir.is_dir():
        for entry in bin_dir.iterdir():
            if not entry.is_symlink() and not os.access(entry, os.X_OK):
                entry.unlink(missing_ok=True)


def _prepare_node_workdir(project_dir: Path, work_dir: Path) -> None:
    shutil.copy2(project_dir / "package.json", work_dir / "package.json")
    for lock_name in (
        "package-lock.json",
        "npm-shrinkwrap.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "bun.lock",
        "bun.lockb",
    ):
        lock_file = project_dir / lock_name
        if lock_file.is_file():
            shutil.copy2(lock_file, work_dir / lock_name)


def _node_install_command(
    package_manager: NodePackageManager,
    *,
    production: bool,
) -> list[str]:
    if package_manager == "npm":
        return ["npm", "install", "--omit=dev"] if production else ["npm", "install"]
    if package_manager == "pnpm":
        # Flags match etersoft-build-utils rpmgs (pnpm section).
        base = ["pnpm", "install", "--frozen-lockfile", "--ignore-scripts"]
        return [*base, "--prod"] if production else base
    if package_manager == "yarn":
        base = ["yarn", "install", "--frozen-lockfile", "--ignore-scripts"]
        return [*base, "--production"] if production else base
    # bun
    return ["bun", "install", "--production"] if production else ["bun", "install"]


def _require_node_package_manager(package_manager: NodePackageManager) -> None:
    hints = {
        "npm": "Установите npm для Node.js-проектов.",
        "pnpm": "Установите pnpm (npm install -g pnpm) для pnpm-проектов.",
        "yarn": "Установите yarn для Yarn-проектов.",
        "bun": "Установите bun для Bun-проектов.",
    }
    require_command(package_manager, hints[package_manager])


def unignore_node_modules_in_gitignore(project_dir: Path) -> int:
    """Comment out node_modules ignore rules so gear can pack them in-tree.

    Used with ``--inplace`` for program packages that ship node_modules in the
    main source tree. Returns the number of lines that were commented out.
    """
    gitignore = project_dir / ".gitignore"
    if not gitignore.is_file():
        return 0

    lines = gitignore.read_text(encoding="utf-8").splitlines(keepends=True)
    changed = 0
    new_lines: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("#"):
            new_lines.append(line)
            continue
        if _NODE_MODULES_GITIGNORE_RE.match(line.rstrip("\n\r")):
            eol = ""
            body = line
            if body.endswith("\r\n"):
                eol = "\r\n"
                body = body[:-2]
            elif body.endswith("\n"):
                eol = "\n"
                body = body[:-1]
            new_lines.append(f"# alt-vendor-export: {body}{eol}")
            changed += 1
        else:
            new_lines.append(line)

    if changed:
        gitignore.write_text("".join(new_lines), encoding="utf-8")
    return changed


def _iter_node_modules_dirs(project_dir: Path) -> list[Path]:
    """Find every ``node_modules`` directory under *project_dir*.

    Nested trees under an already-found ``node_modules`` are skipped so we do
    not walk into store content before deleting the outer tree.
    """
    found: list[Path] = []
    for root, dirnames, _filenames in os.walk(project_dir, topdown=True):
        if "node_modules" in dirnames:
            path = Path(root) / "node_modules"
            found.append(path)
            dirnames.remove("node_modules")
        # Skip gear temp / output trees if present under the project.
        for skip in (".gear", ".git"):
            if skip in dirnames:
                dirnames.remove(skip)
    return found


def _remove_all_node_modules(project_dir: Path) -> None:
    """Remove all in-tree ``node_modules`` before install.

    Workspace packages often keep stale nested ``node_modules`` (wrong owner or
    mode). ``pnpm install`` then fails with ``EACCES`` on unlink/symlink.
    """
    for path in _iter_node_modules_dirs(project_dir):
        try:
            shutil.rmtree(path)
        except OSError as exc:
            raise VendorExportError(
                f"Не удалось удалить {path}: {exc}. "
                "Проверьте владельца/права (часто после сборки от root) и "
                "повторите: chown -R \"$USER\" . && find . -name node_modules "
                "-type d -prune -exec rm -rf {} +"
            ) from exc


def _copy_node_modules(source: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination, symlinks=True)


def _strip_native_binaries(node_modules: Path) -> int:
    """Remove ELF/.node binaries from vendored modules (ALT Node.js Policy).

    Native modules must be separate RPM packages; JS CLI scripts with a shebang
    are left intact.
    """
    if not node_modules.is_dir():
        return 0

    removed = 0
    for path in node_modules.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        drop = path.suffix == ".node"
        if not drop:
            try:
                with path.open("rb") as handle:
                    magic = handle.read(4)
            except OSError:
                continue
            drop = magic == b"\x7fELF"
        if drop:
            path.unlink()
            removed += 1
    return removed


def _install_node_in_workdir(
    project_dir: Path,
    work_dir: Path,
    package_manager: NodePackageManager,
    *,
    production: bool,
    cleanup_dev_packages: bool = False,
    dedupe_system: bool = False,
) -> Path:
    work_dir.mkdir(parents=True, exist_ok=True)
    _prepare_node_workdir(project_dir, work_dir)
    run_command(
        _node_install_command(package_manager, production=production),
        cwd=work_dir,
    )
    if cleanup_dev_packages and package_manager == "npm":
        _remove_dev_packages(work_dir)
    if dedupe_system:
        _deduplicate_system_node_modules(work_dir)
    node_modules = work_dir / "node_modules"
    if not node_modules.is_dir():
        raise VendorExportError(
            f"После установки не найден каталог node_modules в {work_dir}"
        )
    return node_modules


def vendor_node(project_dir: Path, *, inplace: bool = False) -> None:
    """Export Node.js dependencies per ALT Node.js Policy / rpm-build-nodejs.

    Default layout (as in node-mocha / node-webpack)::

        .gear/predownloaded-production/node_modules/
        .gear/predownloaded-development/node_modules/

    Gear packs production modules as a separate Source::

        tar: .gear/predownloaded-production name=@name@-production-@version@ base=

    With ``inplace=True`` (program packages / special hasher builds), also keep
    ``node_modules/`` in the project tree and comment out related ``.gitignore``
    rules.
    """
    package_manager = detect_node_package_manager(project_dir)
    _require_node_package_manager(package_manager)

    gear_dir = project_dir / ".gear"
    # Layout matches lav's packages: no package-name subdirectory.
    dev_target = gear_dir / "predownloaded-development" / "node_modules"
    prod_target = gear_dir / "predownloaded-production" / "node_modules"
    project_node_modules = project_dir / "node_modules"
    dev_work = gear_dir / ".tmp-node-dev"
    prod_work = gear_dir / ".tmp-node-prod"

    for path in (dev_target, prod_target, dev_work, prod_work):
        if path.exists():
            shutil.rmtree(path)

    created_project_modules = False

    if _is_node_workspace(project_dir):
        # Monorepos need an in-tree install (workspace: / catalog protocols).
        # Wipe every nested node_modules — leftover trees with wrong ownership
        # cause EACCES during pnpm/bun link (e.g. .meta-updater/node_modules).
        _remove_all_node_modules(project_dir)
        run_command(
            _node_install_command(package_manager, production=False),
            cwd=project_dir,
        )
        if not project_node_modules.is_dir():
            raise VendorExportError(
                f"После установки не найден каталог node_modules в {project_dir}"
            )
        created_project_modules = True
        _copy_node_modules(project_node_modules, dev_target)
        _copy_node_modules(project_node_modules, prod_target)
        _deduplicate_system_node_modules(prod_target.parent)
    else:
        try:
            dev_modules = _install_node_in_workdir(
                project_dir,
                dev_work,
                package_manager,
                production=False,
                cleanup_dev_packages=True,
            )
            _copy_node_modules(dev_modules, dev_target)

            prod_modules = _install_node_in_workdir(
                project_dir,
                prod_work,
                package_manager,
                production=True,
                dedupe_system=True,
            )
            _copy_node_modules(prod_modules, prod_target)

            if inplace:
                _copy_node_modules(dev_modules, project_node_modules)
                created_project_modules = True
        finally:
            shutil.rmtree(dev_work, ignore_errors=True)
            shutil.rmtree(prod_work, ignore_errors=True)

    _strip_native_binaries(prod_target)
    _strip_native_binaries(dev_target)

    if inplace:
        if not project_node_modules.is_dir():
            _copy_node_modules(dev_target, project_node_modules)
        unignore_node_modules_in_gitignore(project_dir)
    elif created_project_modules and project_node_modules.is_dir():
        # Keep source tree clean for node-* module packaging (mocha style).
        shutil.rmtree(project_node_modules)


def export_vendors(project_dir: Path, *, inplace: bool = False) -> ProjectType:
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
    exporters[project_type](project_dir, inplace=inplace)
    return project_type


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="alt-vendor-export",
        description=(
            "Выгрузка вендоров для Go, Rust, Ruby или Node.js: "
            "vendor/ в дереве проекта (Go/Rust/Ruby) или "
            ".gear/predownloaded-*/node_modules (Node.js)."
        ),
    )
    parser.add_argument("project_dir", help="Путь к каталогу проекта")
    parser.add_argument(
        "--inplace",
        action="store_true",
        help=(
            "Для Node.js: оставить node_modules/ в дереве проекта (офлайн-сборка "
            "в hasher) и закомментировать правила node_modules в .gitignore. "
            "Для Go/Rust/Ruby vendor/ всегда в корне проекта."
        ),
    )
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    project_dir = Path(args.project_dir).resolve()
    try:
        project_type = export_vendors(project_dir, inplace=args.inplace)
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
    if project_type == "node":
        keep = "node_modules"
        print(
            f"Каталоги: .gear/predownloaded-production/{keep} "
            f"и .gear/predownloaded-development/{keep}"
        )
        print(
            "В .gear/rules добавьте:\n"
            "  tar: @name@\n"
            "  tar: .gear/predownloaded-production "
            "name=@name@-production-@version@ base="
        )
    else:
        print("Каталог: vendor/ в дереве проекта")
        if project_type == "rust":
            print("Конфиг cargo: .gear/config.toml")
        print(
            "В .gear/rules:\n"
            "  tar: @version@:.\n"
            "  tar: vendor name=vendor"
        )
    if args.inplace and project_type == "node":
        print(
            "Режим --inplace: node_modules/ в дереве проекта; "
            "правила node_modules в .gitignore закомментированы."
        )
    print("Выгрузка вендоров завершена!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

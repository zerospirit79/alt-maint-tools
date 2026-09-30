"""Dump a pnpm content-addressable store into ``.gear`` for offline hasher builds.

This implements the strategy used by packages that build their frontend with
pnpm inside the hasher (no network access): the whole pnpm store
(content-addressed ``files/`` + ``index.db``) is shipped as a single tarball
(``.gear/pnpm-store.tar``), unpacked into ``.pnpm-store/v<N>`` at ``%build``
time and installed with ``pnpm install --frozen-lockfile --ignore-scripts
--offline``.

Unlike ``alt-vendor-export`` (which follows the ALT Node.js Policy layout
``.gear/predownloaded-*/node_modules``), this command produces a
*content-addressed store* tarball. Because every optional platform-specific
native dependency is preserved (``@rolldown/binding-linux-arm64-gnu``,
``@esbuild/linux-ia32``, …), the same tarball builds on x86_64, i586 and
aarch64 from a single x86_64 machine.

The store format version depends on the pnpm major version (pnpm 11 → ``v11``);
the resulting tarball must match the pnpm version that Sisyphus ships.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

from alt_maint_tools import __version__

DEFAULT_CPU = ["x64", "arm64", "ia32"]
DEFAULT_LIBC = "glibc"
# Store entries that are runtime bookkeeping, not package content, and must not
# be shipped: links/ (hardlink cache, may hold stale pnpm-version cruft),
# .tmp (in-flight downloads) and .pnpm-needs-build-marker.
STORE_EXCLUSIONS = {"links", ".tmp", ".pnpm-needs-build-marker"}


class NodeStoreError(Exception):
    """Raised when the pnpm store dump cannot be completed."""


def _run_capture(
    cmd: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
) -> str:
    """Run a subprocess, return stdout, and raise NodeStoreError on failure."""
    try:
        completed = subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            check=True,
            text=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        message = f"Команда завершилась с ошибкой: {' '.join(cmd)}"
        if detail:
            message = f"{message}\n{detail}"
        raise NodeStoreError(message) from exc
    return completed.stdout or ""


def require_pnpm_project(project_dir: Path) -> None:
    """Validate that *project_dir* looks like a pnpm project."""
    if not project_dir.is_dir():
        raise NodeStoreError(f"Папка проекта не найдена: {project_dir}")
    if not (project_dir / "pnpm-lock.yaml").is_file():
        raise NodeStoreError(
            "pnpm-lock.yaml не найден. alt-node-store работает только с pnpm-проектами."
        )


def pnpm_version() -> str:
    """Return the pnpm version string reported by the installed binary."""
    return _run_capture(["pnpm", "--version"], cwd=Path.cwd()).strip()


def pnpm_store_path(project_dir: Path) -> Path:
    """Return ``pnpm store path`` resolved in the project tree."""
    return Path(_run_capture(["pnpm", "store", "path"], cwd=project_dir).strip())


def supported_architectures_block(cpu: list[str], libc: str) -> str:
    """Render the ``supportedArchitectures`` YAML block for pnpm-workspace.yaml."""
    lines = ["supportedArchitectures:", "  os:", "    - linux", "  cpu:"]
    lines.extend(f"    - {c}" for c in cpu)
    lines.extend(["  libc:", f"    - {libc}"])
    return "\n".join(lines)


def has_supported_architectures(workspace: Path) -> bool:
    """Return True when the workspace file already declares supportedArchitectures."""
    if not workspace.is_file():
        return False
    try:
        content = workspace.read_text(encoding="utf-8")
    except OSError:
        return False
    return "supportedArchitectures" in content


def append_supported_architectures(workspace: Path, cpu: list[str], libc: str) -> None:
    """Append the ``supportedArchitectures`` block to the workspace file.

    Creates the file when missing. The caller is responsible for restoring the
    previous content afterwards.
    """
    original = workspace.read_text(encoding="utf-8") if workspace.is_file() else ""
    content = original
    if content and not content.endswith("\n"):
        content += "\n"
    content += "\n" + supported_architectures_block(cpu, libc) + "\n"
    workspace.write_text(content, encoding="utf-8")


def strip_package_manager(package_json: Path) -> str | None:
    """Temporarily drop ``packageManager`` so pnpm does not self-downgrade.

    pnpm 11 rewrites itself to the version named in ``packageManager`` (e.g.
    ``pnpm@10.17.0``), which then cannot read the newer store. Removing the
    field for the duration of the install avoids that. Returns the previous
    file content for restore, or ``None`` when nothing was changed.
    """
    if not package_json.is_file():
        return None
    try:
        data = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if "packageManager" not in data:
        return None
    original = package_json.read_text(encoding="utf-8")
    del data["packageManager"]
    package_json.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return original


def create_store_tar(store_path: Path, output: Path) -> int:
    """Tar the store contents into *output*, skipping runtime bookkeeping.

    Returns the number of content files packed. *output* is relative to the
    current working directory when not absolute.
    """
    if not store_path.is_dir():
        raise NodeStoreError(f"Не найден store pnpm: {store_path}")
    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with tarfile.open(output, "w") as tar:
        for entry in sorted(store_path.iterdir()):
            if entry.name in STORE_EXCLUSIONS:
                continue
            tar.add(entry, arcname=entry.name, recursive=True)
            if entry.is_dir() and entry.name == "files":
                count = sum(1 for p in entry.rglob("*") if p.is_file())
    return count


def export_pnpm_store(
    project_dir: Path,
    *,
    cpu: list[str],
    libc: str,
    output: Path,
) -> Path:
    """Dump the pnpm store for *project_dir* into ``.gear/pnpm-store.tar``."""
    require_pnpm_project(project_dir)
    if shutil.which("pnpm") is None:
        raise NodeStoreError(
            "pnpm не установлен. Установите pnpm 11 (npm install -g pnpm@11)."
        )

    version = pnpm_version()
    major = version.split(".")[0]
    if major != "11":
        print(
            f"Предупреждение: pnpm {version} (ожидается 11.x, как в Sisyphus). "
            f"Store может не подойти для сборки.",
            file=sys.stderr,
        )

    workspace = project_dir / "pnpm-workspace.yaml"
    package_json = project_dir / "package.json"

    workspace_existed = workspace.is_file()
    workspace_original = (
        workspace.read_text(encoding="utf-8") if workspace_existed else None
    )
    if not has_supported_architectures(workspace):
        append_supported_architectures(workspace, cpu, libc)

    package_original = strip_package_manager(package_json)

    try:
        env = {**os.environ, "CI": "true"}
        _run_capture(
            [
                "pnpm",
                "install",
                "--frozen-lockfile",
                "--ignore-scripts",
                "--trust-lockfile",
            ],
            cwd=project_dir,
            env=env,
        )
        store_path = pnpm_store_path(project_dir)
        target = output if output.is_absolute() else project_dir / output
        count = create_store_tar(store_path, target)
    finally:
        if package_original is not None:
            package_json.write_text(package_original, encoding="utf-8")
        if workspace_existed:
            assert workspace_original is not None
            workspace.write_text(workspace_original, encoding="utf-8")
        else:
            workspace.unlink(missing_ok=True)

    size = target.stat().st_size
    print(f"store: {store_path}")
    print(f"{target} — {size / 1e9:.2f} GB, {count} файлов")
    return target


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="alt-node-store",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Выгрузка pnpm-хранилища (.gear/pnpm-store.tar) для офлайн-сборки "
            "frontend в hasher. Сохраняет нативные биндинги для x86_64/i586/aarch64."
        ),
        epilog=(
            "Когда нужен alt-vendor-export вместо этой утилиты (или вместе с ней):\n"
            "  В spec нет pnpm build / npm run build, node_modules только\n"
            "  распаковываются в hasher  ->  alt-vendor-export: кладёт готовые\n"
            "                                   .gear/predownloaded-*/node_modules\n"
            "                                   (npm, pnpm, yarn 1 и 2+/berry, bun).\n"
            "  В spec есть pnpm build / vite build, т.е. фронтенд собирается\n"
            "  в hasher, где нет сети      ->  alt-node-store (эта утилита,\n"
            "                                   только pnpm).\n"
            "  Нужны и вендоры, и своя сборка -> обе команды, они независимы."
        ),
    )
    parser.add_argument("project_dir", help="Путь к каталогу pnpm-проекта")
    parser.add_argument(
        "--cpu",
        default=",".join(DEFAULT_CPU),
        help=(
            "Список CPU через запятую для supportedArchitectures "
            f"(по умолчанию: {','.join(DEFAULT_CPU)})."
        ),
    )
    parser.add_argument(
        "--libc",
        default=DEFAULT_LIBC,
        help=f"libc для supportedArchitectures (по умолчанию: {DEFAULT_LIBC}).",
    )
    parser.add_argument(
        "--output",
        default=".gear/pnpm-store.tar",
        help="Путь к выходному tar-файлу (по умолчанию: .gear/pnpm-store.tar).",
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
    cpu = [part.strip() for part in args.cpu.split(",") if part.strip()]
    try:
        export_pnpm_store(
            project_dir,
            cpu=cpu,
            libc=args.libc,
            output=Path(args.output),
        )
    except NodeStoreError as exc:
        print(exc, file=sys.stderr)
        return 1
    print("Выгрузка pnpm-хранилища завершена!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

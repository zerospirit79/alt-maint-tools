"""Compare maintainer package versions between Sisyphus and another branch."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import Literal

import requests

DEFAULT_TARGET_BRANCH = "p11"
KNOWN_BRANCHES = (
    "sisyphus",
    "p9",
    "p10",
    "p11",
    "c9f1",
    "c9f2",
    "c10f1",
    "c10f2",
    "c11f1",
    "c11f2",
)

MAINTAINER_API_TEMPLATE = (
    "https://rdb.altlinux.org/api/site/maintainer_packages"
    "?maintainer_nickname={maintainer}&branch={branch}&by_acl=none"
)
REPOSITORY_API_TEMPLATE = (
    "https://rdb.altlinux.org/api/packageset/repository_packages?branch={branch}"
)

CompareStatus = Literal["Совпадает", "Различается", "Отсутствует"]


@dataclass(frozen=True)
class BranchRow:
    name: str
    sisyphus_version: str
    target_version: str
    status: CompareStatus


def get_maintainer_packages_branch(
    maintainer_nickname: str,
    branch: str,
    *,
    session: requests.Session | None = None,
) -> dict[str, str]:
    """Return package name to EVR mapping for a maintainer in a branch."""
    http = session or requests
    response = http.get(
        MAINTAINER_API_TEMPLATE.format(maintainer=maintainer_nickname, branch=branch)
    )
    response.raise_for_status()
    result: dict[str, str] = {}
    for package in response.json().get("packages", []):
        result[package["name"]] = f"{package['version']}-{package['release']}"
    return result


def get_repository_packages(
    branch: str,
    *,
    session: requests.Session | None = None,
) -> dict[str, str]:
    """Return package name to EVR mapping for all packages in a repository branch."""
    http = session or requests
    response = http.get(REPOSITORY_API_TEMPLATE.format(branch=branch))
    response.raise_for_status()
    result: dict[str, str] = {}
    for package in response.json().get("packages", []):
        name = package.get("name", "")
        if not name:
            continue
        result[name] = f"{package.get('version', '')}-{package.get('release', '')}"
    return result


def compare_versions(sisyphus_version: str, target_version: str | None) -> CompareStatus:
    """Compare versions between Sisyphus and the target branch."""
    if target_version is None:
        return "Отсутствует"
    if sisyphus_version == target_version:
        return "Совпадает"
    return "Различается"


def collect_results(
    maintainer_nickname: str,
    target_branch: str,
    *,
    session: requests.Session | None = None,
) -> list[BranchRow]:
    """Build comparison rows for maintainer packages."""
    sisyphus_packages = get_maintainer_packages_branch(
        maintainer_nickname,
        "sisyphus",
        session=session,
    )
    target_packages = get_repository_packages(target_branch, session=session)

    rows: list[BranchRow] = []
    for name, sis_version in sorted(sisyphus_packages.items()):
        target_version = target_packages.get(name)
        status = compare_versions(sis_version, target_version)
        rows.append(
            BranchRow(
                name=name,
                sisyphus_version=sis_version,
                target_version=target_version if target_version is not None else "-",
                status=status,
            )
        )
    return rows


def format_table(rows: list[BranchRow], target_branch: str) -> str:
    """Render a fixed-width text table."""
    target_label = target_branch.upper()
    lines = [
        f"{'Package name':40} {'Sisyphus version':20} "
        f"{target_label + ' version':20} {'Status'}",
        "-" * 100,
    ]
    for row in rows:
        lines.append(
            f"{row.name:40} {row.sisyphus_version:20} "
            f"{row.target_version:20} {row.status}"
        )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Сравнение версий пакетов мейнтейнера в Sisyphus "
            "с выбранной веткой репозитория."
        )
    )
    parser.add_argument("maintainer", help="Ник мейнтейнера в ALT Linux")
    parser.add_argument(
        "-b",
        "--branch",
        default=DEFAULT_TARGET_BRANCH,
        help=(
            "Ветка для сравнения (p9, p10, p11, c9f1, c9f2, c10f1, c10f2, "
            f"c11f1, ...; по умолчанию: {DEFAULT_TARGET_BRANCH})"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    rows = collect_results(args.maintainer, args.branch)
    print(format_table(rows, args.branch))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

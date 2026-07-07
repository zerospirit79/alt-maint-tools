"""Compare maintainer Python package versions in Sisyphus with PyPI."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from typing import Literal

import requests

ALT_API_TEMPLATE = (
    "https://rdb.altlinux.org/api/site/maintainer_packages"
    "?branch=sisyphus&maintainer_nickname={maintainer}&by_acl=none"
)
PYPI_API_TEMPLATE = "https://pypi.org/pypi/{name}/json"

Status = Literal["Совпадает", "Обновить", "Нет на PyPI", "Ошибка PyPI"]


@dataclass(frozen=True)
class PackageRow:
    pypi_name: str
    alt_name: str
    alt_version: str
    pypi_version: str
    status: Status


def strip_alt_tail(version: str) -> str:
    """Remove ALT-specific release suffixes like -alt1 or -alt1.2."""
    return re.sub(r"-alt[\d.]+$", "", version)


def alt_name_to_pypi_name(alt_name: str) -> str | None:
    """Map ALT Python package name to the corresponding PyPI distribution name."""
    if alt_name.startswith("python3-module-"):
        return alt_name[15:]
    if alt_name.startswith("python3-"):
        return alt_name[8:]
    if alt_name.startswith("python-"):
        return alt_name[7:]
    return None


def get_alt_python_packages(
    maintainer_nickname: str,
    *,
    session: requests.Session | None = None,
) -> list[tuple[str, str, str]]:
    """Return (pypi_name, alt_name, evr) tuples for maintainer Python packages."""
    http = session or requests
    response = http.get(ALT_API_TEMPLATE.format(maintainer=maintainer_nickname))
    response.raise_for_status()
    result: list[tuple[str, str, str]] = []
    for package in response.json().get("packages", []):
        name = package["name"]
        pypi_name = alt_name_to_pypi_name(name)
        if pypi_name is None:
            continue
        evr = f"{package['version']}-{package['release']}"
        result.append((pypi_name, name, evr))
    return result


def get_pypi_version(
    pypi_name: str,
    *,
    session: requests.Session | None = None,
) -> str:
    """Fetch the latest PyPI version for a distribution."""
    http = session or requests
    response = http.get(PYPI_API_TEMPLATE.format(name=pypi_name))
    if not response.ok:
        return "Нет на PyPI"
    try:
        return response.json()["info"]["version"]
    except (KeyError, TypeError, ValueError):
        return "Ошибка парсинга"


def get_status(alt_version: str, pypi_version: str) -> Status:
    """Compare ALT and PyPI versions."""
    if pypi_version == "Нет на PyPI":
        return "Нет на PyPI"
    if pypi_version == "Ошибка парсинга":
        return "Ошибка PyPI"
    if pypi_version == strip_alt_tail(alt_version):
        return "Совпадает"
    return "Обновить"


def status_sort_key(row: PackageRow) -> int:
    """Sort rows: updates first, then missing on PyPI, then matching."""
    order = {"Обновить": 0, "Нет на PyPI": 1, "Совпадает": 2}
    return order.get(row.status, 3)


def collect_results(
    maintainer_nickname: str,
    *,
    session: requests.Session | None = None,
) -> list[PackageRow]:
    """Build comparison rows for all maintainer Python packages."""
    rows: list[PackageRow] = []
    for pypi_name, alt_name, alt_version in get_alt_python_packages(
        maintainer_nickname,
        session=session,
    ):
        pypi_version = get_pypi_version(pypi_name, session=session)
        rows.append(
            PackageRow(
                pypi_name=pypi_name,
                alt_name=alt_name,
                alt_version=alt_version,
                pypi_version=pypi_version,
                status=get_status(alt_version, pypi_version),
            )
        )
    rows.sort(key=status_sort_key)
    return rows


def format_table(rows: list[PackageRow]) -> str:
    """Render a fixed-width text table."""
    lines = [
        f"{'PyPI package':25} {'ALT name':30} {'ALT version':15} "
        f"{'PyPI version':15} {'Status'}",
        "-" * 100,
    ]
    for row in rows:
        lines.append(
            f"{row.pypi_name:25} {row.alt_name:30} {row.alt_version:15} "
            f"{row.pypi_version:15} {row.status}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print("Использование: alt-vs-pypi <ник_мейнтейнера>")
        return 1

    rows = collect_results(args[0])
    print(format_table(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

%define _unpackaged_files_terminate_build 1
%define mod_name alt_maint_tools
%def_with check

Name: alt-maint-tools
Version: 0.1.1
Release: alt1
Summary: Utilities for ALT Linux package maintainers
Summary(ru_RU): Утилиты для мейнтейнеров пакетов ALT Linux
License: MIT
Group: Development/Tools
Url: https://github.com/zerospirit79/alt-maint-tools
Vcs: https://github.com/zerospirit79/alt-maint-tools
BuildArch: noarch

Source: %name-%version.tar

BuildRequires(pre): rpm-build-pyproject
BuildRequires: python3(setuptools)
BuildRequires: python3(wheel)
BuildRequires: python3(requests)

Requires: python3 >= 3.9
Requires: python3-base
Requires: python3-module-requests


# Optional runtime tools for alt-vendor-export (checked per project type)
Requires: golang
Requires: rust
Requires: ruby
Requires: node
Requires: npm
Requires: gem-bundler

# cargo vendor is built into cargo in Sisyphus; enable with -with cargo_vendor on stable branches
%if_with cargo_vendor
Requires: cargo-vendor
%endif

%if_with check
BuildRequires: python3-module-pytest
BuildRequires: python3-module-pytest-mock
%endif

%description
A set of command-line utilities for ALT Linux package maintainers:
comparison of Python package versions with PyPI, branch version comparison
via RDB API, and vendor export for Go, Rust, Ruby, and Node.js projects.

%description -l ru_RU
Набор консольных утилит для мейнтейнеров пакетов ALT Linux: сверка версий
Python-пакетов с PyPI, сравнение версий между ветками репозитория и выгрузка
вендоров для Go, Rust, Ruby и Node.js проектов.

%prep
%setup -q

%build
%pyproject_build

%install
%pyproject_install

%check
%pyproject_run_pytest

%files
%doc LICENSE README.md
%_bindir/alt-vs-pypi
%_bindir/alt-branch-compare
%_bindir/alt-vendor-export
%python3_sitelibdir/%mod_name/
%python3_sitelibdir/%{pyproject_distinfo %mod_name}/

%changelog
* Tue Jul 07 2026 Pavel Shilov <zerospirit@altlinux.org> 0.1.1-alt1
- Initial build for Sisyphus.

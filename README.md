# alt-maint-tools — utilities for ALT Linux package maintainers

Набор консольных утилит для мейнтейнеров пакетов ALT Linux:

- **alt-vs-pypi** — сверка версий Python-пакетов в Sisyphus с PyPI;
- **alt-branch-compare** — сравнение версий пакетов мейнтейнера между Sisyphus и стабильной веткой;
- **alt-vendor-export** — выгрузка вендоров для Go, Rust, Ruby и Node.js проектов.

## Установка

### Из исходников

```bash
git clone https://github.com/zerospirit79/alt-maint-tools.git
cd alt-maint-tools
pip install .
```

### Из репозитория ALT Linux

```bash
apt-get install alt-maint-tools
```

## Использование

### Сверка с PyPI

```bash
alt-vs-pypi zerospirit
```

Скрипт получает список Python-пакетов мейнтейнера из Sisyphus через [RDB API](https://rdb.altlinux.org/api/site/maintainer_packages) и сравнивает версии с PyPI. Суффиксы вида `-alt1` при сравнении отбрасываются.

### Сравнение веток

```bash
# Сравнение с p11 (по умолчанию)
alt-branch-compare zerospirit

# Сравнение с другой веткой
alt-branch-compare zerospirit --branch c10f2
alt-branch-compare zerospirit -b p9
```

Поддерживаются ветки `p9`, `p10`, `p11`, `c9f1`, `c9f2`, `c10f1`, `c10f2`, `c11f1`, `c11f2` и любые другие, доступные в RDB.

### Выгрузка вендоров

```bash
alt-vendor-export /path/to/project
```

Тип проекта определяется автоматически по наличию `go.mod`, `Cargo.toml`, `Gemfile` или `package.json`.

Для Rust:

- в Sisyphus используется встроенная команда `cargo vendor`;
- в p10/p11 при сборке RPM-пакета включите `-with cargo_vendor`, либо установите `cargo-vendor` вручную.

Для Node.js вендоры складываются в `.gear/predownloaded-development/` и `.gear/predownloaded-production/`.

## Разработка

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[test]"
pytest
```

## Сборка RPM в ALT Linux

В каталоге `.gear/` находится spec-файл для сборки через GEAR:

```bash
gear-update-tag
gear-build -src -build
```

## Лицензия

MIT

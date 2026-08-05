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

#### Go

```bash
alt-vendor-export /path/to/go-project
```

Выполняются `go mod tidy` и `go mod vendor`. Каталог `vendor/` появляется в дереве проекта.

#### Rust

```bash
alt-vendor-export /path/to/rust-project
```

- в Sisyphus используется встроенная команда `cargo vendor`;
- в p10/p11 при сборке RPM включите `-with cargo_vendor` либо установите `cargo-vendor` вручную.

#### Ruby

```bash
alt-vendor-export /path/to/ruby-project
```

Зависимости ставятся через Bundler в `vendor/bundle`.

#### Node.js

Выгрузка следует [Node.js Policy](https://www.altlinux.org/Node.js_Policy) ALT Linux
и раскладке пакетов вроде [`node-mocha`](https://git.altlinux.org/gears/n/node-mocha.git):
зависимости кладутся в отдельный Source через `.gear/predownloaded-*`, а не
коммитятся в основной tar исходников.

```bash
# Модуль node-* (mocha, webpack, eslint, …) — режим по умолчанию
alt-vendor-export /path/to/node-module

# Программа (pnpm, bun, …): node_modules в дереве исходников для hasher
alt-vendor-export --inplace /path/to/program
```

Менеджер пакетов выбирается по lock-файлам:

| Lock / маркер | Команда |
|---|---|
| `bun.lock` / `bun.lockb` | `bun install` |
| `pnpm-lock.yaml` / `pnpm-workspace.yaml` | `pnpm install` |
| `yarn.lock` | `yarn install` |
| иначе | `npm install` |

**Режим по умолчанию** (модули `node-*`):

- `.gear/predownloaded-production/node_modules` — production-зависимости;
- `.gear/predownloaded-development/node_modules` — полный набор (для сборок с devDeps);
- из production убираются пакеты, уже есть в `%nodejs_sitelib` (`/usr/lib/node_modules`);
- из вендоров удаляются ELF и `.node` (нативные модули — отдельные RPM по политике).

Пример `.gear/rules`:

```
tar: @name@
tar: .gear/predownloaded-production name=@name@-production-@version@ base=
```

Фрагмент spec:

```spec
Source: %name-%version.tar
Source1: %name-production-%version.tar

BuildRequires: rpm-build-nodejs node
BuildRequires(pre): rpm-macros-nodejs

%prep
%setup -a 1

%install
mkdir -p %buildroot%nodejs_sitelib/%node_module/
cp -a * %buildroot/%nodejs_sitelib/%node_module/
```

**Режим `--inplace`** (программы в `/usr/bin`, офлайн-сборка в hasher):

- дополнительно оставляется `node_modules/` в корне проекта;
- строки с `node_modules` в `.gitignore` комментируются (`# alt-vendor-export: …`),
  чтобы gear мог упаковать модули в основной tar.

По политике в `node_modules` программы не должно быть нативных бинарников —
такие зависимости оформляются отдельными пакетами `nodejs-<имя>`.

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

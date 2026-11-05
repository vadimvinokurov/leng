# CI/CD и развертывание Leng: настройка с нуля

Дизайн и обоснования: `docs/superpowers/specs/2026-11-05-release-mirror-design.md` (untracked).
Коротко: клиенты собираются GitHub Actions по тегу `v*`; сервер раздаёт
зеркало релиза своей версии; клиент обновляется по кнопке в трее строго
до версии сервера; деплой — `deploy.sh` / `deploy.bat`.

Одна и та же версия везде: тег `v1.2.3` == поле `version` в
`client/src-tauri/tauri.conf.json` == версия в `GET /v1/health`. Префикс `v`
срезается автоматически (скриптом и гарда релиза), поэтому сервер видит
версию `1.2.3`, а не `v1.2.3`.

## 1. Ключи подписи апдейтов (один раз)

tauri-plugin-updater требует minisign-подпись. Генерация:

```
cargo install tauri-cli --locked
mkdir .leng-signing
cargo tauri signer generate -w .leng-signing/leng.key
```

Каталог `.leng-signing/` игнорируется git — приватный ключ в репозиторий не
попадает. Пароль ключа запоминается отдельно.

## 2. GitHub secrets (настраиваемая часть, руками)

Settings → Secrets and variables → Actions:

- `TAURI_SIGNING_PRIVATE_KEY` — содержимое `.leng-signing/leng.key`
- `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` — пароль ключа

Публичный ключ (`.leng-signing/leng.key.pub` content) вписать в
`client/src-tauri/tauri.conf.json` → `plugins.updater.pubkey` **вместо
плейсхолдера**. Пока стоит плейсхолдер: релизы собираются, но встроенные
клиенты молча не находят обновления.

## 3. Первый деплой (чистая машина с Docker)

```
git clone https://github.com/vadimvinokurov/leng.git leng
cd leng
cp /path/to/production.env .env      # владелец, руками; см. .env.example по именам
./deploy.sh                          # Linux/macOS; на Windows: deploy.bat
```

Ожидаемо: скрипт проверил `.env`, собрал образ, поднял контейнер и напечатал
адрес. `curl http://localhost:28080/v1/health` → `{"status":"ok","version":"dev"}`
(тегов пока нет). Страница `http://localhost:28080/` покажет ссылку на
GitHub Releases, пока зеркальный кэш пуст.

Опционально в `.env`: `LENG_GITHUB_REPO=<owner>/<repo>` — только если релизы
раздаются из форка (дефолт `vadimvinokurov/leng`).

## 4. Релиз (каждый раз)

1. Bump версии в **трёх** файлах (гард релиза не пустит расхождение):
   `client/src-tauri/tauri.conf.json` (`version` — источник updater-манифеста),
   `client/src-tauri/Cargo.toml` (`version` — от него `X-Client-Version`
   у GUI-клиента) и `client/core/Cargo.toml` (`version` — от него
   `leng-cli`). Коммит в `main`.
2. `git tag v1.2.3 && git push origin main --tags` (тег == версия после
   среза `v`, иначе гард релиза не пустит).
3. Actions → `release`: два job'а (macOS aarch64, Windows msvc) публикуют
   релиз с ассетами и `latest.json` сразу (не драфт).
4. На сервере: `git pull --ff-only && ./deploy.sh`. Сервер поднимается
   версии `1.2.3` и при старте зеркалит релиз `v1.2.3` себе.
5. Проверка: клиент со старой версией при следующем запросе получает
   «ваша версия не совпадает…», в трее появляется «Обновить приложение до
   v1.2.3»; клик — скачивание, установка, рестарт.

Пока сервер не обновлён, свежий релиз лежит в GitHub и ни на что не влияет:
все клиенты говорят со старой версией сервера и работают тихо.

## 5. Раздача конфига людям (вне программы)

`config.json` кладётся вручную рядом с исполняемым файлом
(`client/config.json.example` — образец): `backend_url` — адрес сервера,
`api_token` — значение `LENG_API_TOKEN` из серверного `.env`. Токен один
общий; скачивание сборок с сервера открыто всем, API — только с токеном.

## 6. Ручной чек-лист GUI (§11.2 п.7 — после первого живого релиза)

- [ ] Запуск без config.json: HUD-ошибка «нет конфига…», не падение.
- [ ] Клиент своей версии: тихая работа, обновления в трее нет.
- [ ] Клиент чужой версии: после деплая новой версии сервера — трей-пункт
      «Обновить приложение до vX.Y.Z»; HUD-запрос показывает «обновите
      приложение».
- [ ] Клик по пункту: сборка скачивается с сервера, устанавливается,
      клиент перезапускается уже новой версии.
- [ ] Обычный чек §11.2 п.7: хоткей, event tap-права, фокус, позиция HUD.

## 7. Что где лежит

- `.github/workflows/release.yml` — сборка клиентов по `v*` (два runner'а).
- `.github/workflows/ci.yml` — полный локальный гейт (`./scripts/test.sh`)
  на каждый push/PR в `main`.
- `deploy.sh` / `deploy.bat` — деплой сервера (`.env` кладётся владельцем
  заранее, скрипт останавливается с ошибкой, если его нет).
- Зеркало релиза: `${LENG_DATA_DIR}/clients/` на хосте = `/data/clients`
  в контейнере; обновляется при старте релизного сервера и вручную
  `POST /v1/clients/refresh` (с токеном и своей версией в заголовке).

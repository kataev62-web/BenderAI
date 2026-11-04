# Итог интеграции ECC — 04.11.2026

## Установка и безопасность

- Официальный `ecc@ecc` **2.2.3** установлен через Codex marketplace из `affaan-m/ECC`, закреплённого SHA `b38be6a7f6350964d59328b52f0624e1d1d552d1`.
- CLI: `0.166.0-alpha.1`. Установка через `plugin add` поддерживается актуальным Codex; документация ECC требует 0.146.0+ и не использует старый `plugin install`.
- Повторный marketplace add вернул `alreadyAdded: true`; в installed inventory ровно один `ecc@ecc` версии 2.2.3, enabled=true. Конфликтующего legacy sync и проектных AGENTS/.codex файлов до этой работы не найдено.
- Официальный cache checker: все ссылки манифеста на skills/MCP/hooks/assets разрешаются внутри установленного кэша. Хэши манифеста, Codex hook definition и MCP config совпадают с проверенным upstream.
- Свежий native app-server `skills/list`: **350 ECC-навыков + 9 ранее имеющихся built-ins**, ошибок чтения 0 в каждом проекте. Документация/манифест рекламирует 281; отчёт использует наблюдаемые 350, не выдавая их все за исполненные.
- Проверенные effective settings из `config/read`: hooks=false; ECC enabled=true; Chrome DevTools MCP enabled=false. Никакой hook trust не выдавался, hooks/MCP не исполнялись, npm зависимости и старый sync не устанавливались.
- Первый вариант установки отклонён автоматической проверкой. После дополнительного аудита был отдельно одобрен безопасный вариант: сначала постоянное отключение hooks и browser MCP, затем официальный plugin add. Ограничение hooks глобально для этого native CLI; перед изменением не было установленного native plugin, пользовательского hook-файла или явного hooks preference. Другие модели, sandbox, approvals и пользовательские настройки не менялись.
- Проекты пока untrusted для native CLI, поэтому проектные .codex слои **игнорируются**. Автодоверие не выставлялось. Действуют уже проверенные user-level guards; проектные файлы дублируют их для будущих доверенных сессий и не форсируют plugin enablement.
- Текущий долгоживущий cloud chat не перезагружает собственный каталог инструментов автоматически. Для использования native ECC skill surface откройте новую соответствующую Codex-сессию. Наличие в skills/list и проверка кэша не означают выполнения/семантической проверки всех 350 навыков.

## Проектные изменения

Добавлены `AGENTS.md`, `.codex/config.toml`, `.github/PULL_REQUEST_TEMPLATE.md`, `docs/ecc/{WORKFLOW.md,SECURITY_REVIEW.md,STATUS.md,upstream.json,UPSTREAM_LICENSE.txt}`. Ранее существующих файлов с этими именами не было; README, runtime, тесты, зависимости, build scripts и workflows сохранены. В проекты не скопированы 350 навыков, hooks, MCP launcher, чужие агенты или глобальные ECC profiles; полный официальный bundle существует только в штатном пользовательском plugin cache.

Выборочные процессы: `ecc:verification-loop`, `ecc:security-review`, `ecc:tdd-workflow` и подходящие языковые рекомендации. Java service-примеры не превращают Forge в Spring/Quarkus; generic npm/TypeScript/coverage команды не навязываются Python/Java проектам. Отдельного нативного Codex code-review role/Claude slash-command не объявляем: review lanes заданы в AGENTS/PR-шаблоне, независимое ревью текущего diff выполнено.

Сканирование секретов выводит только имена файлов/категории; `auth.json`, значения env/ключи и пользовательские session memories не читались/не копировались. Установка не переносится автоматически в локальный Codex или любую новую cloud machine: локальные шаги/аудит и проверка версии описаны в WORKFLOW.md. Автоматической установки/обновления/доверия проектные файлы не выполняют.

## BENDER AI: фактические проверки

База: `dc81a544f7b03e87f1096b34717e0218e322bc1d`. Runtime и текущая разработка не менялись.

| Проверка | Итог |
|---|---|
| `QT_QPA_PLATFORM=offscreen /workspace/bender-venv/bin/python -m pytest -q` | **40 passed, 3 skipped**, 0 failed; те же Windows-only пропуски, что до интеграции |
| `python -m pip check` | PASS, No broken requirements found |
| `python -m compileall -q src tests scripts` | PASS, exit 0 |
| Новая Windows-сборка для ECC-only изменений | Не запускалась: приложение/зависимости/build scripts/workflows не менялись. Не объявляется новой проверкой exe |
| Ранее выполненная Windows-валидация | Ссылки и точные SHA в `docs/WINDOWS_BUILD_REPORT.md`: 40 portable Windows passed + 1 неприменимый skip, packaged exe smoke passed, отдельные 2 native UIA passed |
| Интерактивный Windows desktop в этом ECC-этапе | NOT RUN; Linux, native CI и ручная приёмка трея/hotkey/DPI/click-through разделены |

Существующие Windows package/native workflows и ограниченная модель безопасности сохранены. Ревью и proof-of-build направлены на текущие инструкции, будущие изменения — на реальные тесты и matching SHA/артефакты, а не на автоматические обещания готовности от ECC.

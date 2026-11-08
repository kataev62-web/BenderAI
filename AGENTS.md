# Правила для Codex — экономный режим

- Выполняй утверждённую задачу целиком, без запросов «продолжать?» и без дробления на искусственные этапы.
- Не пиши приветствий, длинных планов, рассуждений и повторяющихся промежуточных сообщений. Сообщай о ходе работы только при блокировке, риске или необходимости согласования.
- Изучай только файлы, нужные для задачи; ищи точечно. Не перечитывай весь репозиторий и историю без причины.
- Делай минимальные достаточные изменения; не рефактори соседний код и не добавляй новые документы или зависимости без необходимости.
- Не запускай параллельных агентов и повторных проверок без реальной пользы.
- Проверяй изменённую функциональность подходящими тестами. Полную сборку запускай только по необходимости; важные проверки не пропускай.
- Не выдавай непроверенное за работающее. Если тест невозможен, прямо укажи почему.
- В конце дай ОДИН краткий отчёт (до 5 строк): сделано; проверено; ограничения. Без пересказа процесса.

## Проект
Python 3.12, PySide6, Windows 10/11. Сначала смотри README.md; документацию открывай только по теме задачи. Для проверок предпочитай целевой pytest. Не объявляй Windows UI/захват действий подтверждёнными по одному Linux-тесту. Сохраняй явное согласие пользователя на действия с ОС и безопасный глобальный стоп.

## Сохранённые инженерные ограничения ECC

# BENDER AI — engineering instructions

Read `README.md`, `docs/ARCHITECTURE.md`, `docs/SECURITY.md`, the latest build report and any more specific instructions before editing. Preserve current development, user changes and existing project rules.

## Scope and invariants

- Python **3.12** for supported development/CI, PySide6 **6.8.3**, SQLite, Windows 10/11 x64; use `pyproject.toml` plus `constraints.txt` and the existing virtual environment. Do not upgrade or add dependencies merely to match a generic ECC example.
- Tooling/ECC changes do not change application behavior, permission policy, model/provider selection, character, schema, training data or user settings.
- AI providers propose validated typed plans; only the registry/executor and permission checks may dispatch actions. Preserve deny-by-default access, one-shot confirmation, sequential execution, cancellation, deadlines, process isolation and password exclusions.
- Keep GUI responsive and OS access behind reviewed adapters. No unrestricted shell/eval, arbitrary generated commands, hidden input capture, silent screenshot/cloud upload or bypass of UAC/permission controls.
- Credentials belong in Windows Credential Manager, never repository/config/plaintext files. Synthetic test values are not real credentials. Reports must not include credential values or user screenshots/data.

## Verification and TDD

Use the curated [ECC adaptation](docs/ecc/WORKFLOW.md); it supplements the separately installed official ECC plugin and is not a copied plugin or installer. For a behavior fix, reproduce the failure with a meaningful regression test before changing code. Do not add tests that simply grep prose or mirror constants. Never invent coverage or enforce ECC's example 80% threshold without configuring and measuring it separately.

From the repository root:

```bash
python -m pip install -c constraints.txt -e '.[dev]'
python -m pip check
python -m compileall -q src tests scripts
# Linux / headless portable verification:
QT_QPA_PLATFORM=offscreen python -m pytest -q
```

On Windows, with the selected Python/venv:

```powershell
python -m pytest -m "not windows" -q
$env:BENDER_WINDOWS_INTEGRATION = '1'
python -m pytest -m windows -v
powershell -File scripts/build_windows.ps1 -Python .venv\Scripts\python.exe
```

- Keep `.github/workflows/windows.yml` (package + packaged smoke) and `windows-native.yml` (native Win32/UIA) intact. Any future Linux engineering checks complement them, never replace them.
- `scripts/ci_summary.py` redacts diagnostics. Distinguish actual passing/skipped/deselected cases. Never count zero selected tests as validation.
- Windows build success requires both exe files, retained runtime libraries, current smoke JSON, test XML and build SHA. Preserve PyInstaller's bounded startup probe and `freeze_support()` before GUI imports.
- A Linux/Qt offscreen pass is not Windows UIA evidence. A native CI form test is not a human test of tray, global hotkey, click-through, mixed DPI or the complete packaged GUI.
- Run real-provider/Gradle/manual desktop checks only when their prerequisites and authorization exist. Keep mock/HTTP-fixture tests distinct from real Ollama/cloud/model or JDK/Gradle results.

## Debugging, review and delivery

Separate Python/Qt startup, packaging/import/COM issues, timer resolution, UIA selectors/events, SQLite migration/persistence and permission failures. Use current test XML, packaged-smoke JSON and the matching Actions SHA; do not remove assertions or widen permissions to make CI green.

Review correctness, thread/process lifetimes, cancellation before/after dispatch, privacy/redaction, input schemas, canonical paths, local memory and dependencies. Run `git diff --check`, inspect changed files and preserve all user changes. Tooling-only work must leave `src/`, `tests/`, constraints, build scripts and the existing Windows workflows unchanged unless the user explicitly included those surfaces.

No automatic commits/push/merge/release, global hooks, external MCP, learned-memory import or sandbox/approval changes are implied by reading these instructions. Reuse existing authorization for the user's concrete task. Official `ecc@ecc` 2.2.3 is installed in this cloud CLI; fresh Codex discovery exposes its skills. Select only `ecc:verification-loop`, `ecc:security-review`, `ecc:tdd-workflow` and `ecc:python-testing` where relevant. User-level hooks and ECC Chrome DevTools MCP are off; project `.codex/config.toml` repeats these guards while inheriting plugin enablement and other settings. Project config requires normal project trust and is not currently effective in this untrusted cloud CLI; do not grant trust automatically. Never bypass a rejected install through legacy sync or hook-trust bypass flags.

Final report: actual changes; commands/counts and failures/skips; source/build SHA and artifact; review findings; remaining manual acceptance. See `docs/WINDOWS_MANUAL_TEST.md` for Windows user testing. Keep production-ready claims separate from a successful CI candidate.

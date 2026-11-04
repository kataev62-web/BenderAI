## Scope

Describe the final behavior or engineering change. For ECC/tooling work, preserve ongoing application development and existing Windows build/acceptance workflows.

## Evidence

- Source / build SHA and Actions links:
- Portable pytest: passed / failed / skipped / deselected
- pip check / compileall:
- Native Windows UIA tests: result or NOT RUN
- Packaged exe smoke / ZIP / metadata: result or NOT RUN
- Manual tray / hotkey / DPI / click-through / packaged learning: result or NOT RUN
- Coverage / lint / types: measured results or NOT CONFIGURED

## Review

- [ ] Registry/schema/permissions and explicit confirmations preserved
- [ ] GUI threads, COM processes, stop/deadlines and persistence checked where relevant
- [ ] Passwords, API keys, UI values and diagnostics reviewed for privacy
- [ ] No arbitrary shell, automatic cloud/screenshot, unsafe hooks/MCP or approval bypass
- [ ] User changes and existing Windows workflows preserved; diff reviewed

## Limitations

Separate Linux/Qt fixtures, native CI tests, real provider tests and user desktop acceptance. Never claim CI alone validates the complete Windows desktop.

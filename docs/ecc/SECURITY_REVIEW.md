# ECC security review — hooks and MCP disabled

Source reviewed: `affaan-m/ECC@b38be6a7f6350964d59328b52f0624e1d1d552d1`, version 2.2.3. Native manifest uses the repository root, skills, `hooks/codex-hooks.json` and `.mcp.json`. This review identifies reachable entry-point effects; it is **not a complete audit of all upstream skills, dependencies or future releases**.

| Surface | Observed behavior | Access / decision |
|---|---|---|
| Codex marketplace | `.agents/plugins/marketplace.json` points to the repository root | Official source registered at the reviewed SHA; ECC separately installed through native plugin add |
| Codex SessionStart | Node bootstrap -> plugin-hook-bootstrap -> session-start-bootstrap -> run-with-flags -> session-start | Not trusted, enabled or executed |
| Filesystem | Session directories/learned skills read; directories created; observer lease written; expired `*-session.tmp` files removed by default after 30 days | Scope extends outside the current project into ECC/Claude agent-data locations; explicit review/trust needed |
| Context | Previous session/learned instinct text can be injected into the model context | Untrusted/stale-context and cross-project privacy considerations; not activated |
| Processes / environment | Child Node processes inherit environment; Git identity/package-manager detection and helper code are reachable | No secret values inspected; environment inheritance is not a safe secret allowlist |
| Network | No direct HTTP upload was observed in the inspected session-start entry path; helpers/other skills are not proven network-free | Do not claim all ECC is offline or safe from a limited static review |
| Bundled MCP | `npx -y chrome-devtools-mcp@1.10.1` | Would download/execute npm code and expose browser/debugging capability with inherited environment; not needed, not started |
| Other hooks | Claude `hooks.json` has different profiles/events from the Codex projection | Never copy it into Codex or claim Claude hook-profile parity |
| Legacy sync/global git hooks | Alternative mutation paths exist upstream | Not executed; must not be used to work around a rejected install |

Codex documents separate plugin enablement and per-hash hook trust. No bypass flag, broad permission, global trusted-project setting, model/profile override, secret binding or external MCP was added. Repo-local hook/MCP guards are prepared but native config/read currently reports the cloud projects untrusted and ignores that layer. User-level hook/MCP restrictions are already effective; the ignored project overlay is not presented as active enforcement.

## Automatic approval outcomes

The initial proposed native installation was rejected by automatic approval review: disabling only the browser MCP did not cover persistent lifecycle hooks. That compound command never executed, so it neither wrote its proposed MCP policy nor installed ECC. No indirect workaround or hook-trust bypass was used.

A later, materially safer operation was approved after additional audit and state checks. The CLI had no installed plugins, no user hook file and no explicit hooks preference. The approved operation first set **features.hooks=false** using the native `codex features disable hooks`, then persisted ECC Chrome DevTools MCP **enabled=false**, verified both controls and performed the official plugin add with hooks disabled for the invocation too. The plugin is now officially installed/enabled; fresh native discovery finds 350 ECC skills. Cached manifest, hook definition and MCP config hashes match the audited upstream. The full official bundle is cached once outside the projects; no bulk skill/rule copying into either repo occurred.

Lifecycle hooks are globally disabled for this native CLI, including any hooks added later until the user revisits that policy; this is an intentional restriction, not an assertion that trust alone blocks execution. The Chrome DevTools MCP is disabled independently. No model, sandbox, approval policy, secret, custom pre-existing hook setting or trusted-project entry was changed. Repo-local config repeats these two restrictions for future trusted sessions, while global plugin enablement stays inherited. The current cloud project layers are untrusted/ignored; the effective user-level restrictions are verified and no ECC hook/MCP was executed.

The currently running cloud chat's tool catalog is not automatically replaced by the new native installation. Runtime skill discovery was tested in a fresh app-server without model requests; execution of all individual skills, hooks and MCP is not claimed. These guards resolve the initially rejected operation's runtime-activation risk; the rejection was not bypassed by legacy sync, a shadow plugin or disabled approval review.

## Update and credential policy

Review changed manifests, hooks and their reachable helpers, dependency install scripts, MCP launchers and file/network targets before updating or trusting a new hash. Keep normal TLS, sandbox and approval controls. Do not run `npm ci`, npm post-install hooks, old sync or global git-hook installers just to make a cache check green. If runtime packages are later required, follow the reviewed upstream instructions (`--ignore-scripts`, exact installedPath/version) with explicit authorization.

Do not read/copy Codex `auth.json`, environment values or credential files for setup. Scan only names/presence and use the native CLI's existing authentication for normal authorized operations. Secret scanning should report filenames/categories, not matched secret lines. Secrets and user session memories never belong in a repository or an ECC prompt.

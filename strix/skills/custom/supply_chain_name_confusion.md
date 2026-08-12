---
name: supply-chain-name-confusion
description: npx/dependency confusion playbook — find command and package names a target resolves from a public registry but nobody owns (npx/bunx/dlx fallback, scoped-package bin mismatch, internal deps), verify claimability non-destructively, and report only proven-reachable cases
---

# Supply Chain Name Confusion (npx / dependency confusion)

A target is vulnerable when a name it *executes or installs* is resolved from a
**public** registry and that name is **not owned by the target**. Whoever
registers the name first gets arbitrary code execution on developer laptops,
CI/CD runners, release pipelines, and AI coding agents — with whatever tokens
those environments hold. There is no CVE, no vulnerable version, and SCA tools
miss it completely: the dependency is not vulnerable, the *name resolution* is.

Four distinct findings, in descending signal:

| Type | Condition |
|---|---|
| **npx confusion** | `npx <cmd>` where `<cmd>` is not resolvable locally, so npm installs the public package literally named `<cmd>` |
| **bin mismatch** | A scoped package `@org/foo-tool` ships `"bin": {"foo-tool": ...}`. `bin` keys cannot contain `/`, so docs/scripts say `npx foo-tool` → resolves the **unscoped** name, which the org usually never registered |
| **dependency confusion** | An internal/`workspace:`/`file:` dependency name that resolves publicly when the private registry is missing, misconfigured, or lower-priority |
| **name clash** | The name exists publicly but is owned by an unrelated third party — the target already executes someone else's code |

This skill is **npm-name-resolution** focused, and deliberately concrete: the
decision procedure below is what separates a real finding from an unowned name
that nothing actually resolves. Related skills, and where the boundary sits:

- `dependency_cve_scanning` — known-CVE dependency versions. Different finding
  class, different report tool.
- `infrastructure_lifecycle` — the general ownership-continuity model (domains,
  MX, update endpoints, buckets). Load it when the target trusts an abandoned
  *endpoint* rather than an unowned *name*.
- `agentic_system_security` — MCP/agent component supply chain. Load it when the
  question is the agent's effective authority; come here for who owns the
  package name its `command: npx -y <name>` resolves.
- `semantic_confusion` — the general "two components disagree about a
  representation" model, of which scoped-package-vs-unscoped-bin is one case.
- CI/CD workflow abuse (`pull_request_target`, mutable PR merge refs, cache
  poisoning) is a separate class — do not fold it in here.

## How npx Resolves a Command

npm CLI (`libnpmexec`) tries, in order:

1. `node_modules/.bin` walking up from cwd (local install)
2. the global bin dir / global `node_modules`
3. the npx cache (`_npx`)
4. **fetch from the configured registry** the package literally named after the
   command, install it, then execute its bin

Step 4 is the vulnerability. Two properties make it worse than it looks:

- In a **non-TTY / CI** context npm does not prompt — it logs a warning and
  installs. `-y` / `--yes` (extremely common in CI and in MCP server configs)
  removes the prompt everywhere.
- The registry is hit **before** the prompt/`--no` check, so the resolution
  target is observable without ever installing anything.

Equivalents to cover: `npm exec`, `bunx`, `pnpm dlx`, `yarn dlx`,
`deno run npm:<name>`. Same class in other ecosystems: `uvx <name>` /
`pipx run <name>` (PyPI dist name vs `console_scripts` name — identical
mismatch bug), implicit `docker.io/library/<image>`, devcontainer features,
and `uses: org/repo@ref` in GitHub Actions when the org/repo was renamed.

## Phase 1 — Collect Candidates (with execution context)

Record for every candidate: **name, file, line, and whether it is executed**.
Context is what separates a finding from noise later, so never collect a bare
name list.

```bash
ART=/workspace/.strix-namecheck; mkdir -p "$ART"

# Executed invocations — the primary vector
rg -n --no-heading -g '!node_modules' \
  -e '\b(npx|bunx)\s+(-{1,2}[a-zA-Z-]+(=\S+)?\s+)*[@a-zA-Z0-9._/-]+' \
  -e '\b(pnpm|yarn)\s+dlx\s+\S+' \
  -e '\bnpm\s+exec\s+\S+' \
  . > "$ART/invocations.txt"
```

Cover every executable surface, not just `package.json` scripts:

- `package.json` `scripts` (incl. `pre*`/`post*` hooks), `Makefile`, shell
  scripts, `Dockerfile` `RUN`, `.husky/`, `lint-staged`, Turbo/Nx task defs
- CI: `.github/workflows/*.yml` `run:` steps, composite actions, GitLab CI,
  Jenkinsfiles — highest impact, these hold registry/cloud credentials
- **MCP / agent configs**: `.mcp.json`, `.cursor/mcp.json`, `.vscode/mcp.json`,
  `claude_desktop_config.json`, `devcontainer.json`. These are almost always
  `npx -y <name>` and are executed by an agent with no human review
- `README`/docs install snippets — real risk (humans and agents paste them),
  but lower confidence than a CI step; grade accordingly
- `bin` maps of every package the target *publishes* (walk its npm scope:
  `https://registry.npmjs.org/-/org/<org>/package`), plus local workspace
  `package.json` files
- `dependencies`/`devDependencies` entries using `file:`, `link:`,
  `workspace:`, or a scope that only exists internally
- Black-box targets: exposed `/package.json`, `/package-lock.json`,
  `/npm-shrinkwrap.json`, source maps (`sources[]`), and webpack module paths
  (`node_modules/<name>/`) in served bundles. Capture bundles from real traffic
  (agent-browser HAR) rather than scraping HTML — lazily loaded chunks hold the
  internal names, and parse them as JS (AST) instead of regexing minified text

Drop immediately, before any registry traffic: Node builtins (`fs`, `node:*`),
names containing shell/template expansion (`$VAR`, `{{`, backticks), paths
(`./x`, `/x`), flags, and anything failing npm name rules (lowercase, ≤214
chars, no leading `.`/`_`, URL-safe).

## Phase 2 — Prove the Name Reaches the Public Registry

Do this **before** checking availability. It is the gate that kills most noise:
if the command resolves locally, there is no registry fallback and no finding.

```bash
# A. What does the bare command actually resolve to? Clean dir, no deps.
cd "$(mktemp -d)" && npx --no <cmd> 2>&1 | grep -E '404|GET https'
# → "404 Not Found - GET https://registry.npmjs.org/<cmd>" proves npx maps the
#   bare command to exactly that public package. Non-destructive: npm resolves
#   the manifest before the install prompt, so nothing is installed.

# B. Does the target's own environment fall back? Repo root, after normal install.
cd /workspace/<repo> && npm ci && npx --no <cmd> --version
# → succeeds  = satisfied by node_modules/.bin  → NOT a finding in this context
# → E404/ENOENT = falls back to the registry     → finding stands
```

`npx --no` from a subdirectory still walks up to the workspace root, so a
locally installed bin is safe from any cwd inside the project. Treat a
locally-satisfied command as a finding **only** when you can point at an
execution path outside that installed tree (a bootstrap script run before
`npm ci`, a docs snippet a user runs in `$HOME`, an MCP config on a developer
machine) — and grade it lower.

## Phase 3 — Determine Ownership and Claimability

```bash
curl -s -o /tmp/pkg.json -w '%{http_code}\n' \
  "https://registry.npmjs.org/$(printf '%s' "$NAME" | sed 's|@|%40|; s|/|%2f|')"
```

| Response | Meaning | Action |
|---|---|---|
| `404 {"error":"Not found"}` | unregistered | candidate finding — continue to the gates below |
| `200`, `dist-tags.latest` = `0.0.1-security` (single stub version, npm-owned) | npm **security holding** placeholder | **not claimable** → not a finding. Strong evidence the name was already abused/reserved; report at most informationally |
| `200`, maintainers/repository belong to the target | target owns it | not a finding — drop silently |
| `200`, unrelated owner | **name clash** — target executes third-party code | finding only if Phase 2 passed; check publish date, weekly downloads, and whether the code is malicious/squatting |
| `401`/`403` | private or blocked scope | inconclusive — do not report |
| `429`/`5xx`/timeout/DNS failure | **unknown** | never treat as unregistered; retry later, otherwise record as a scan limitation |

Two claimability gates that npxconfuse-style tooling gets wrong:

- **npm typosquat/moniker rule**: a *new* package name is rejected if, with
  punctuation (`. - _`) removed, it collides with an existing package. So a
  `404` name can still be unregisterable. Check the punctuation-stripped form
  and plausible punctuation variants; if one exists, the attack fails — downgrade
  to informational. `npm publish --dry-run` does **not** perform this check.
- **Unpublished names**: `name@version` can never be reused, and a fully
  unpublished package's name is blocked for 24h. A `404` on a name whose GitHub
  history shows it once existed needs this called out in `assumptions`.

Verify the checker itself before trusting any `404` — a proxy, mirror, or
offline sandbox can turn every lookup into a uniform answer:

```bash
for n in lodash strix-sentinel-$(uuidgen | tr 'A-Z' 'a-z'); do
  echo "$n $(curl -s -o /dev/null -w '%{http_code}' https://registry.npmjs.org/$n)"
done
# MUST print 200 then 404. Anything else → your results are meaningless; stop.
```

Re-confirm every `404` at least twice, spaced out, and cross-check with
`npm view <name> versions --json`.

## Phase 4 — Report

**Never publish, reserve, or squat a name on a public registry**, and never
ship a canary/callback payload — that is an attack on a third party and on the
target's users. All evidence here is non-destructive. If the target explicitly
authorizes a defensive placeholder publish, that is remediation work, not
validation, and needs written authorization first.

File with `create_vulnerability_report`, one report per **unique name** with
every occurrence aggregated in `code_locations` (never one report per line).

The PoC is the two-part resolution proof, which is fully reproducible and
requires no malicious package: (1) `npx --no <cmd>` in a clean directory
showing `GET https://registry.npmjs.org/<cmd>` → 404, i.e. the command resolves
to an unowned public name; (2) the target's own execution path (CI step, MCP
config, docs command) that runs it. State plainly in `assumptions` that code
execution follows from an attacker registering the name and that no package was
published during testing.

Severity — derive it from the environment that executes the command:

- **Critical/High**: unregistered name reached from CI/CD, release, or
  publish-time execution, or from an MCP/agent config or bootstrap script that
  runs on developer machines. Full code execution with pipeline credentials
  (`AV:N`, `PR:N`, `C:H/I:H/A:H`; `UI:R` when a human or agent must trigger it)
- **Medium**: name clash — the target already executes an unrelated owner's
  package, but you have not shown that package is malicious
- **Low/Informational**: docs-only mention with no executed path; the command
  is satisfied locally in every real execution context; the name is npm-held,
  blocked by the moniker rule, or private-scope
- **Not a finding**: registry lookup inconclusive, target owns the name, or
  Phase 2 showed no registry fallback

## False Positives — Hard Gates

Every one of these has burned automated name-confusion scanners:

1. **Locally satisfied commands.** `npx tsc`, `npx eslint`, `npx vite` in a repo
   that declares them are resolved from `node_modules/.bin`. Phase 2B is
   mandatory, not optional.
2. **Popular public tools.** A `200` for `prettier`/`tsc`/`jest` is the real
   tool by its real maintainer, not a clash. Do not report ecosystem-standard
   binaries as name clashes without evidence of ownership change.
3. **Non-confusable invocation forms.** `npx @scope/pkg`, `npx --package
   @scope/pkg <bin>`, `npx pkg@1.2.3` where the org owns `pkg`, and any
   `--registry`-pinned or `.npmrc` scope-routed call. Regex-matching `npx \S+`
   flags all of these.
4. **`.npmrc` scope routing.** If `@org:registry=` points at a private registry
   and the internal scope is fully qualified, dependency confusion for that
   scope does not apply. Read `.npmrc`, `.yarnrc.yml`, and CI registry setup
   before reporting internal-dependency findings.
5. **Registry errors read as availability.** Rate limits, proxy blocks, and
   egress restrictions are not `404`. Run the sentinel check.
6. **npm-held / unregisterable names.** `0.0.1-security` stubs and
   moniker-rule collisions look claimable but are not.
7. **Names the target already owns.** Compare maintainers, repository URL, and
   scope before assuming a third party controls a name.
8. **Bundle-extraction garbage.** Minified identifiers, CSS class names, and
   chunk fragments are not package names. Require an AST-level
   `require`/`import`/module-path context, then the npm-name-validity filter.
9. **Upstream, not the target.** An unowned name inside a third-party
   dependency's manifest is that maintainer's exposure. Note it separately;
   do not file it against the target.
10. **Duplicates.** Dedupe by name across all sources before any registry
    traffic and before reporting.

## Remediation

Ordered by durability:

1. Invoke the full package name: `npx --package @org/foo-tool foo-tool`, or
   rename the `bin` so it matches, or drop `npx` and call the local binary
   (`node_modules/.bin/foo-tool`, `npm run`).
2. Add `--no` (`npx --no <cmd>`) so a missing local install fails loudly
   instead of silently installing from the public registry; use
   `npm ci`-installed devDependencies in CI, never on-the-fly `npx -y`.
3. Register the unscoped names the org's docs and `bin` maps tell people to
   run, as placeholders pointing at the scoped package.
4. Route internal scopes explicitly (`@org:registry=`) and ensure the private
   registry does not fall through to the public one for internal names.
5. Pin versions, commit lockfiles, and prefer `--offline`/vendored installs in
   privileged pipelines.
6. Audit MCP/agent configs for `npx -y <bare-name>` — an agent will run them
   without asking.

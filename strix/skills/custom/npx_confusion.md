---
name: npx-confusion
description: Test npx and npm exec binary-name confusion where a missing local executable is reinterpreted as a public npm package name, including scoped-package bin mismatches, CI and agent invocations, resolution-context analysis, and false-positive elimination
---

# npx Confusion

Use this skill when a target invokes a bare command through `npx` or `npm exec` and the intended executable name may differ from the package that provides it. This is narrower than classic dependency confusion: the issue is the transition from **unresolved binary name** to **remotely fetched package spec**.

Load `dependency_cve_scanning` for known vulnerable versions, `infrastructure_lifecycle` for abandoned domains or registry resources, `agentic_system_security` for the authority of an MCP/agent process, and `semantic_confusion` for the general lookup-order model.

## Core Condition

Require all of the following:

1. A target-controlled workflow invokes `npx <name>`, `npx -y <name>`, or an equivalent `npm exec` form.
2. `<name>` is not resolved as an executable in the workflow's real local/global context.
3. npm consequently interprets `<name>` as a package spec and consults the configured registry.
4. The resolved public name is unintended, unregistered, or controlled by a party other than the intended publisher.
5. The affected workflow actually reaches the fetched package's executable.

A public package merely being outside the target's ownership is not a vulnerability. Third-party packages are normal; the mismatch between intended executable provenance and actual registry resolution is the finding.

## Resolution Model

Record the npm version because `npx` has used `npm exec` since npm 7 and resolver behavior changes between releases. For current npm, model these decisions:

```text
bare command
  -> executable in ancestor node_modules/.bin?
  -> executable in global bin?
  -> matching local/global package and usable bin?
  -> matching environment in the npx cache?
  -> treat the command token as a package spec
  -> fetch its manifest from the configured registry
  -> infer one executable from package.json#bin
  -> install into the npx cache and execute
```

Also record:

- working directory and workspace root
- local dependency tree and generated `node_modules/.bin` links
- global prefix/bin directory and npx cache
- `registry`, scope-specific registry rules, proxy and authentication configuration
- command form, flags, package spec/version, TTY/CI state, and `yes` policy
- npm's executable-inference result when the package exposes zero, one, or several `bin` entries

Do not collapse package-name lookup and bin selection into one step. npm can fetch a manifest yet fail because it cannot infer exactly one executable.

## High-Signal Patterns

### Bare executable fallback

```text
npx internal-tool
npx -y internal-tool
npm exec -- internal-tool
```

The signal is strongest in CI, release scripts, bootstrap commands, developer setup, and tool/agent configuration where the same command is run repeatedly.

### Scoped package versus unscoped bin

A scoped package can expose an unscoped executable:

```json
{
  "name": "@org/tooling",
  "bin": { "org-tool": "./bin/run.js" }
}
```

Inside a correctly installed workspace, `npx org-tool` may resolve `node_modules/.bin/org-tool`. Outside that tree, the same command can fall back to the public package named `org-tool`. Treat documentation, MCP configuration, and bootstrap scripts as separate execution contexts rather than assuming the repository-local result applies everywhere.

### Agent and MCP launchers

Inspect `.mcp.json`, editor/desktop agent configuration, devcontainers, and generated tool launchers for `command: npx` plus `-y` and a bare package or binary name. Combine this resolver analysis with `agentic_system_security` to determine the credentials, tools, files, and network access inherited by that process.

## Candidate Collection

Search executable surfaces and retain file, line, command, and execution context:

```bash
rg -n --no-heading -g '!node_modules' \
  -e '\b(npx|npm\s+exec)\s+[^[:space:]]+' \
  -e '"command"\s*:\s*"npx"' \
  -e '"args"\s*:\s*\[[^]]*"-y"' \
  package.json package-lock.json npm-shrinkwrap.json \
  .github .gitlab-ci.yml Jenkinsfile Dockerfile Makefile \
  .mcp.json .cursor .vscode 2>/dev/null
```

Also inspect:

- package scripts and lifecycle hooks
- workspace package `name` and `bin` maps
- READMEs and generated setup instructions
- CI composite actions and reusable workflows
- source maps or bundled package metadata that reveal internal commands

Discard paths, shell variables, flags, Node built-ins, and text that is not executed or presented as an executable command.

## Establish the Actual Resolution

Prefer inspecting the existing dependency tree, lockfile, workspace packages, and `.bin` links. Do not run `npm ci` merely to decide whether a command is local: it changes the tree and can execute lifecycle scripts.

For a version-controlled reproduction environment, record npm's registry lookup without allowing a missing package to be installed:

```bash
npx --no --loglevel=http <candidate>
```

Interpret this carefully:

- a local executable may run immediately; `--no` only refuses missing-package installation
- an HTTP registry request shows fallback, not ownership or successful execution
- a cancellation naming the missing package shows npm's chosen package spec
- cache, global installs, parent directories, workspaces, and registry configuration can change the result

Repeat the resolution analysis in every context that matters: repository root, documented launch directory, CI checkout, generated agent configuration, and bootstrap-before-install flow. Do not substitute a clean empty directory for the target context except to understand npm's generic name mapping.

## Ownership and Registry State

Query the exact registry selected by the target configuration, then distinguish:

- intended package owned by the expected publisher
- unrelated public package with the same name
- unregistered name (`404` from a functioning registry)
- private or access-controlled name (`401`/`403`)
- transient/rate-limited/blocked lookup (`429`, `5xx`, timeout)
- placeholder, reserved, disputed, or previously unpublished name

A `404` proves absence from that registry at that time; it does not by itself prove that registration would be accepted. Registry similarity, trademark, reservation, security-hold, and unpublish rules remain separate facts. Inspect maintainers and ownership metadata rather than treating a version such as `0.0.1-security` as conclusive by itself.

## Validation and Impact

Demonstrate the complete resolver statement:

```text
target-controlled invocation and context
  -> intended executable absent
  -> exact public package spec selected
  -> package ownership/availability state
  -> execution trigger and inherited authority
```

Do not report an unregistered name without an execution path, or an execution path whose command is satisfied locally in every relevant context. Derive impact from the environment that executes the package: developer workstation, CI job, release pipeline, agent runtime, container build, or documentation-only workflow.

## False Positives

- The executable is provided by a declared dependency in every real execution context.
- `npx --package @scope/pkg <bin>` explicitly binds the executable to the intended package.
- A versioned package spec or scope-specific registry points to the intended publisher.
- The public package is the deliberately selected third-party tool.
- npm fetches the manifest but cannot infer or execute a bin.
- The reference appears only in generated/minified text with no executable call site.
- A registry/proxy error is misread as an unregistered name.
- A package is absent but registry policy prevents the contested registration.

## Remediation

- Install the intended package and invoke its local executable through an npm script.
- Bind the command explicitly: `npx --package @org/tool org-tool`.
- Use `--no` where a missing local dependency must fail instead of fetching.
- Route private scopes to the intended registry and prevent public fallback.
- Pin package versions and lockfiles in privileged workflows.
- Replace bare `npx -y <name>` agent launchers with reviewed, publisher-qualified, version-pinned package specs.

## Summary

Treat npx confusion as an execution-context bug: an unresolved executable is reinterpreted as a package name and fetched from a registry. Prove each resolver transition, distinguish binary names from package names, and evaluate every working directory and automation context independently.

---
name: argument-injection
description: Argument injection and argument splitting testing for CLI/subprocess invocations, including flag/option smuggling, argv boundary breakout, response/config file abuse, and Windows Best-Fit (WorstFit) charset transformations that defeat prior escaping
---

# Argument Injection

Use this skill when user-influenced data becomes part of a command's **argument vector**, not a shell string. This is distinct from classic command injection: there may be no shell, no metacharacters, and correct shell-escaping — yet the attacker still controls program behavior by injecting **additional flags/options** or by splitting one argument into several.

The core question is never "can I reach a shell?" It is: *does attacker input decide which options, files, or sub-actions a trusted binary performs?* Load `rce` when a shell metacharacter sink is present, and `semantic_confusion` when the injection arises from a normalization/encoding differential between the escaper and the argv consumer.

## Why It Is Missed

- The code uses a safe API (`execve`, `subprocess.run([...])`, `ProcessBuilder`) with no shell, so command-injection checks pass.
- Each individual argument is correctly quoted/escaped for the shell, but quoting does not stop a value that *starts with `-`* from being parsed as an option.
- The input passes a WAF/validator in one representation, then a later layer (OS, C runtime, wide→ANSI conversion) rewrites it into argv-significant characters.

## Attack Surface

Look for any place a trusted binary is invoked with attacker-influenced values:

- image/media processors: `convert`/ImageMagick, `ffmpeg`, `gs`/Ghostscript, `exiftool`
- VCS and transfer tools: `git`, `svn`, `hg`, `curl`, `wget`, `scp`/`ssh`/`plink`, `rsync`
- archive/crypto/db tools: `tar`, `zip`/`unzip`, `openssl`, `gpg`, `mysql`/`psql`, `sqlite3`
- interpreters/runtimes launched as subprocesses: `php`, `php-cgi`, `python`, `node`, `java`
- mail/report/PDF pipelines, LDAP/`ldapsearch`, `find`/`xargs`, and any `Open With`/handler registration
- CGI/FastCGI query strings mapped onto interpreter argv (e.g. historical `php-cgi` `?-d`/`-s`)

## Two Distinct Primitives

### 1. Option/Flag Injection

A value placed where a *positional* argument is expected but not prefixed-guarded is parsed as an option:

- write primitives: `--output=`, `-o`, `-O`, `--config=`, `-K/--config`, `--upload-file`
- read/exec primitives: `--exec`, `-c`, `-e`, `--use-compress-program=`, `--checkpoint-action=exec=`
- behavior toggles: `--insecure`, `--no-check-certificate`, `-proxy`, `--interactive`

Representative generic PoCs (validate on the specific tool/version — flag names vary):

```text
# curl: turn a fetched "URL" into a file write / local file read
-o/tmp/pwn            # write response to a chosen path
file:///etc/passwd    # scheme downgrade when scheme is not pinned

# tar: classic exec via checkpoint action
--checkpoint=1 --checkpoint-action=exec=sh\ shell.sh

# git: option-controlled config / hook / upload-pack
-c core.sshCommand=... ext::sh\ -c\ ...
```

### 2. Argument Splitting

One intended argument becomes several because a separator survives escaping:

- whitespace, `\t`, newline, or NUL that the escaper missed
- quoting that the argv builder collapses differently than the validator expected
- an OS/runtime transformation that *introduces* a separator (see Best-Fit below)

The result: `["tool", "user-value"]` becomes `["tool", "user", "--evil"]`.

## Windows Best-Fit / "WorstFit" Charset Transformation

A critical, widely-missed argument-injection amplifier on Windows. ANSI (`*A`) APIs convert UTF-16 to the process code page using **Best-Fit mapping**, which silently rewrites Unicode look-alikes into argv-significant ASCII *after* validation and escaping have run.

Affected APIs (any of these can undo prior sanitization):

- `GetCommandLineA`, `CommandLineToArgvA`-style parsing, `__argv`/`main(argc, argv)` in ANSI builds
- `GetEnvironmentVariableA`, `getenv`, `GetCurrentDirectoryA`, `getcwd`
- `FindFirstFileA`/`FindNextFileA` and other `*A` filesystem calls

Best-Fit turns benign-looking Unicode into delimiters/flags depending on code page:

| Attacker sends (Unicode) | Best-Fit result | Effect |
|---|---|---|
| U+00AD soft hyphen | `-` | injects an option where `-` was filtered |
| U+FF0F fullwidth solidus, ¥/₩ (yen/won) | `/` or `\` | path traversal / flag separators |
| U+2033, fullwidth quotes | `"` | breaks out of a quoted argv segment |
| various fullwidth/look-alike letters | ASCII letters | reconstruct filtered keywords |

Consequences seen in research: PHP-CGI argument-injection bypass via soft hyphen, path traversal via yen/won/fullwidth slash, argv splitting despite prior escaping, and env/path confusion in CGI. The invariant: **the bytes validated are not the bytes the program parses.**

## Detection and Recon

- Source review: find every `subprocess`/`exec*`/`ProcessBuilder`/`os.popen`/backtick site and check whether any argument is attacker-influenced and whether a leading-`-` guard or `--` terminator precedes it.
- Black-box: submit values beginning with `-`/`--`, embedding whitespace/newline/NUL, and (on Windows targets) Unicode look-alikes for `- / \ "`. Diff behavior, output location, timing, and error text against a clean baseline.
- CGI/interpreter surfaces: probe whether query strings without `=` reach interpreter argv (historical `php-cgi` `?-s`, `?-d allow_url_include=1`).
- Prefer a benign, observable primitive first (write a canary to a tester-owned path, add a no-op flag that changes output verbosity) before any exec flag.

## Safe Validation

1. Prove input crosses the argv boundary: show the same value parsed as an option/extra arg vs. treated as a literal positional (paired control).
2. Use the least powerful demonstrable primitive — a verbose/version flag or a write to a tester-owned path — not remote code execution, unless RCE proof is explicitly authorized and contained.
3. For Best-Fit, capture both the submitted Unicode bytes and the ANSI bytes the process actually parsed (e.g. via a logging shim or the tool's own echo of argv), and record the code page.
4. Reproduce on the deployed tool/runtime version; flag names, Best-Fit tables, and CGI behavior are version- and code-page-specific.

## Defenses (for remediation notes)

- Prefix untrusted positional values with `--` (end-of-options) where the tool supports it, or hard-pin every option yourself.
- Reject or normalize leading `-`, whitespace, and NUL in values destined for argv.
- On Windows, use wide-character APIs (`wmain`, `GetCommandLineW`, `*W` calls) and avoid ANSI/Best-Fit conversion entirely.
- Never build argv from user input for security-relevant flags (output paths, config, exec/hook options); pass those as fixed literals.

## False Positives

- Value is attacker-influenced but the code inserts `--` before it, or validates a strict allowlist (numeric/UUID/enum) that cannot start with `-`.
- A separator appears in logs but the argv builder passes the whole value as one element (verify the real `argv`, not the log line).
- A Unicode character is accepted but the target uses `*W` APIs, so no Best-Fit conversion occurs.
- The injected flag exists but has no security-relevant effect on this tool/version.

## Pro Tips

1. The tell is a trusted binary + user-controlled argument, even with no shell and perfect quoting.
2. Always test a value that simply *starts with a dash*; it is the highest-signal, lowest-effort probe.
3. On Windows, treat `*A` APIs as an escaping-bypass primitive, not a cosmetic detail — Best-Fit runs after your validation.
4. Generalize findings by the primitive class (write / read / exec / behavior toggle), not by the specific flag string.
5. CGI query strings that reach an interpreter's argv are argument injection, not "just LFI."

## Summary

Argument injection is control of a program's argument vector without needing a shell. Model where untrusted data enters `argv`, test for option smuggling and argument splitting, and remember that Windows Best-Fit conversion can reintroduce `- / \ "` after every validation step. Prove the argv boundary crossing with a paired control and the least powerful primitive.

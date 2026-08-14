---
name: owasp-top-10-testing
description: Test an application against the OWASP Top 10 with Strix — autonomous AI agents that attempt real exploits for each category (broken access control, cryptographic failures, injection, insecure design, misconfiguration, vulnerable components, auth failures, integrity failures, logging gaps, SSRF) and report only what they could actually prove, mapped back to the category with a proof-of-concept. Also covers the OWASP API Security Top 10. Use when the user asks for an OWASP Top 10 assessment, OWASP compliance testing, or a security review mapped to OWASP categories.
license: Apache-2.0
metadata:
  author: usestrix
  homepage: https://docs.strix.ai
---

# Test against the OWASP Top 10

The OWASP Top 10 is a taxonomy of risk categories, not a test suite — so "OWASP Top 10 testing" means exercising each category against the real application and reporting what's actually exploitable. Strix's agents do the exploitation; this skill covers running it category-by-category and honestly reporting coverage.

Install, LLM setup, and the managed-cloud alternative: **penetration-testing-with-strix**.

## What is and isn't testable by an agent

Be straight with the user about this — claiming a clean sweep of all ten is misleading.

| Category (2021) | Coverage |
|---|---|
| A01 Broken Access Control | **Strong** — needs two accounts (and a privileged one) to prove cross-user/tenant and privilege-escalation access. |
| A02 Cryptographic Failures | **Partial** — transport config, weak/absent encryption of data in transit, tokens and secrets exposed in responses; at-rest crypto needs source or infra review. |
| A03 Injection | **Strong** — SQL/NoSQL/command/template injection and XSS, exploit-validated. |
| A04 Insecure Design | **Partial** — business-logic abuse (price/quantity tampering, workflow skipping, race conditions) is found where reachable; design intent still needs human review. |
| A05 Security Misconfiguration | **Strong** — debug endpoints, verbose errors, permissive CORS, missing hardening, default credentials, exposed admin surfaces. |
| A06 Vulnerable & Outdated Components | **Partial** — version fingerprinting plus dependency review when source is supplied; use a dedicated SCA tool for exhaustive dependency inventory. |
| A07 Identification & Auth Failures | **Strong** — auth bypass, weak session/token handling, password-reset and MFA flaws. |
| A08 Software & Data Integrity Failures | **Partial** — insecure deserialization and unsigned-update paths where reachable; CI/CD supply-chain integrity is out of scope for a runtime scan. |
| A09 Logging & Monitoring Failures | **Not testable from outside** — requires reviewing the logging/alerting pipeline; state this rather than reporting it as passed. |
| A10 SSRF | **Strong** — exploit-validated, including blind SSRF via out-of-band callbacks. |

For APIs, run the same exercise against the **OWASP API Security Top 10** (BOLA, broken function-level authz, mass assignment, excessive data exposure) using the **api-security-testing** skill.

## Run it

Maximum category coverage comes from giving the agents both the source and a running instance, plus credentials at two privilege levels:

```bash
strix -n \
  -t https://github.com/org/app \
  -t https://staging.example.com \
  --scan-mode deep --max-budget 30 \
  --instruction "OWASP Top 10 (2021) assessment. Cover every category systematically and map each finding to its category.
Accounts: userA@example.com/<pw> (org 1), userB@example.com/<pw> (org 2), admin@example.com/<pw>.
Prioritise A01 (cross-org and privilege escalation), A03, A05, A07, A10.
Out of scope: /billing/*, outbound email."
```

- `--scan-mode deep` matters here: systematically walking ten categories is not a quick scan.
- Without a second account, A01 results are structurally incomplete — say so in the report rather than leaving it implied.
- Need an auditor-facing PDF mapped to categories? Run it through the managed platform and pull the technical report (**managed-pentesting-with-strix**).

## Report honestly

From `strix_runs/<run>/`, group `vulnerabilities/*.md` by OWASP category and state, per category: what was attempted, what was proven, and what couldn't be assessed (A09 always, A02/A06/A08 partially). Verify each PoC yourself before it goes in front of the user.

A `0` exit code means nothing exploitable was proven **in what was analyzed** — check `run.json` status and cost against `--max-budget`; a budget-capped run is not a completed assessment.

## Then fix and re-test

Remediate with **fix-security-vulnerabilities-with-strix** and re-run to prove each exploit is closed. For ongoing coverage as the app changes, gate pull requests using **ci-security-scanning-with-strix**.

---
name: capture-decision
description: >
  Use when a decision has just been made or is being discussed — an approach
  chosen over alternatives, a constraint accepted, a thing deliberately not
  done. Captures it as a why record: a small markdown file, in
  this repository, that says what was decided and why, in the shape the
  Whyspec spec defines and Attrica reads. Trigger whenever work settles a
  question that someone could later reopen without knowing it was settled.
---

# Capture a decision as a why record

A codebase remembers what it does and forgets why. When a decision lands —
in a conversation, a review, a refactor — capture it before it evaporates.
One decision, one file, a minute of writing.

## Where it goes

`docs/decisions/YYYY-MM-DD-<question-slug>.md`

The date is the capture date (UTC). **The slug names the question, never the
chosen answer** — `2026-08-18-where-does-session-state-live.md`, not
`2026-08-18-use-the-primary-database.md`. Two different questions can share
an answer, and answer-named files silently overwrite each other. Slug rule:
lowercase, every run of characters outside `a–z0–9` becomes one `-`, no
leading or trailing `-`. Once written, never rename the file.

## The shape

```markdown
# Decision: Store session state in the primary database

**Status:** Accepted
**Date:** 2026-08-18

## Context

Where should session state live? The shared cache is already deployed and
the primary database is not sized for per-request reads.

## Decision

Sessions live in the primary database. A cache eviction would log every
user out, and the reconnect storm costs more than the reads it saves.

## Alternatives considered

1. Shared cache keyed by session id — rejected: eviction logs everyone out.
2. Signed client-side cookie — deferred: revisit once payload size settles.
```

That is a complete record. Rules that make it one:

- **The title is the chosen option**, as prose, after `# Decision: `. Never
  a template token — a title containing `TITLE`, `NNN`, `YYYY-MM-DD`, or a
  `[bracketed placeholder]` marks the file as an unfilled template and it is
  not a record at all.
- **`**Status:**` then `**Date:**`**, adjacent lines, directly under the
  title. Status is one of `Draft`, `Proposed`, `Accepted`, `Rejected` — and
  **never `Superseded`**: whether a record is current is derived from
  supersession relations, not written by hand, and a hand-written
  `Superseded` is silently read as live. If a record is replaced, the NEW
  record says `supersedes` (see below); the old one is left untouched.
- **The date is a full `YYYY-MM-DD`**, calendar-valid. `2026-8-1`,
  `2026/08/01`, and `2026-12` all read as *no date* and quietly break
  recency ordering.
- **`## Context` holds the question; `## Decision` holds the answer and the
  why.** Write the reason into the Decision section — the constraint being
  paid for, the failure being avoided. A decision without its why is what
  this convention exists to prevent.
- **Alternatives are a flat list**, one per line, each optionally carrying a
  disposition: `<option> — <disposition>: <reason>` where the disposition is
  exactly one of `rejected`, `deferred`, `partially-adopted`,
  `not-evaluated`, and the dash has a space on **both** sides. Any other
  word, or a glued dash, makes the whole line read as plain option text.
  `deferred` is not a soft `rejected` — it means the option is still open.

## Optional, when they earn their place

- `**Id:** <any stable identifier>` beside Status and Date — written once,
  never changed, unique in the corpus.
- `## Governs` — a list of paths or globs this decision governs, one per
  item, written **verbatim** (`src/**/*.py` keeps both asterisks; no bold,
  no reformatting). An absent section means scope-undeclared, which is
  broader than an empty one.
- **Supersession**: the replacing record writes `supersedes ADR-0007` (or
  the old record's Id) in prose or under `## Relations`. Give ADR-numbered
  files four-digit numbers (`0007`) in their filenames, or the reference
  cannot resolve.

## Two traps

- **Never demonstrate record syntax inside a record.** A fenced code block
  containing `**Status:** Rejected` or a `## Decision` heading can be read
  as the host record's own structure and flip or erase it. Show syntax in
  ordinary documentation files, never in a record.
- **Do not write `(none recorded)` placeholders** into optional sections —
  omit empty sections entirely. Only `## Context` and
  `## Alternatives considered` may carry `(none recorded)`, and only when
  their absence is itself the honest answer.

## Make it stick

The record above is the act. The convention is one line in this repository's
agent instructions (`AGENTS.md`, `CLAUDE.md`, or whatever your client
loads):

> A decision worth keeping gets a why record in `docs/decisions/`, in
> the shape the capture-decision skill teaches.

Add that line once and every future session inherits the habit. A minimal
record — a `# Decision:` title, the Status and Date lines, and a
`## Decision` section carrying its why — already counts; everything else
here is what makes it carry more.

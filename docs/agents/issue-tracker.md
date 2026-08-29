# Issue tracker: GitHub

Durable issues and specifications for this repository live in GitHub Issues.

The canonical repository is `skhlo/touchsignal`, numeric repository ID
`1350293098`. Its `origin` remote uses SSH. Before each tracker write, verify
that the repository identity remains exact and the active GitHub account is
`skhlo`.

## Local session context

Keep private working context under `.scratch/`.

The directory is local-only and ignored by Git. It may contain session notes,
draft issue bodies, temporary investigation results, and restart context.

Before publishing information from `.scratch/`:

1. Remove private and unrelated information.
2. Rewrite the content in plain English that follows ASD-STE100 principles.
3. Keep only the facts needed to understand or reproduce the work.
4. Inspect the final body file before passing it to `gh`.

Never publish credentials, tokens, usernames, hostnames, home-directory paths,
serial numbers, network addresses, private account data, or unrelated logs.

## GitHub conventions

Use the `gh` CLI for tracker operations.

- Create issue and pull-request bodies in a local file.
- Read the body and confirm a known substring before publishing it.
- Create an issue with `gh issue create --title "..." --body-file <file>`.
- Read an issue with `gh issue view <number> --comments`.
- List issues with `gh issue list`.
- Comment with `gh issue comment <number> --body-file <file>`.
- Apply labels with `gh issue edit <number> --add-label "..."`.
- Remove labels with `gh issue edit <number> --remove-label "..."`.
- Close an issue with `gh issue close <number>`.
- Read the resulting issue after every write.

## Pull requests as a triage surface

PRs as a request surface: no.

External pull requests do not enter the triage queue automatically. An
explicitly named pull request can still be reviewed when the user requests it.

## Skill operations

When a skill says "publish to the issue tracker", create a GitHub issue from an
inspected body file.

When a skill says "fetch the relevant ticket", read the GitHub issue and its
comments.

A bare issue number can refer to an issue or pull request because GitHub shares
one number space. Resolve the type before acting.

## Wayfinding

Use one issue labelled `wayfinder:map` as the map. Use linked child issues as
tickets.

Prefer GitHub sub-issues and native issue dependencies. If those features are
unavailable, use task-list links and a `Blocked by:` line in the child issue.

A ticket is available when it is open, has no open blocker, and has no
assignee. Claim it by assigning the authenticated user. Resolve it by posting
the sanitized result and closing the issue.

# AGENTS.md - TouchSignal

## Agent skills

### Issue tracker

Track durable, sanitized work in GitHub Issues. Keep private session context under the local-only `.scratch/` directory. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the five default Matt Pocock triage labels. See `docs/agents/triage-labels.md`.

### Domain docs

Use a single-context layout with `CONTEXT.md` and `docs/adr/` at the repository root. See `docs/agents/domain.md`.

## GitHub communication

- Write GitHub content in plain English that follows ASD-STE100 principles.
- Include only information needed to understand or reproduce the work.
- Do not publish credentials, private session context, usernames, hostnames, local paths, device identifiers, network details, or unrelated command output.
- Relevant product targets such as `MacBookPro16,1`, Omarchy, and `linux-t2` are not private when needed to describe TouchSignal.
- Draft GitHub bodies in a file, inspect them, and confirm a known substring before publishing them with `gh --body-file`.
- Read the resulting GitHub artifact after every write.
- Do not perform a GitHub write until this repository has a verified remote and repository identity.

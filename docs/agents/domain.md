# Domain documentation

TouchSignal uses a single-context domain layout.

## Before exploring

Read these sources when they exist:

- `GLOSSARY.md`
- relevant ADRs under `docs/adr/`

Proceed silently when they do not exist. Domain-modeling work creates them only
when the project resolves terms or architectural decisions that need durable
ownership.

## Layout

```text
/
├── GLOSSARY.md
├── docs/
│   └── adr/
└── src/
```

## Vocabulary

Use terms defined in `GLOSSARY.md` in issues, specifications, tests, and code.
Do not replace established terms with unrecorded synonyms.

If a required concept is missing, determine whether the proposed term is
unnecessary or whether the glossary has a real gap.

## Architectural decisions

Read the ADRs that affect an area before changing it. If proposed work
contradicts an ADR, identify the conflict explicitly instead of silently
overriding the decision.

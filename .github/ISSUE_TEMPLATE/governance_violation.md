---
name: Governance violation
about: A governance rule appears to be violated or an invariant is failing.
title: "[governance] "
labels: governance, priority-high
---

## Rule affected

<Which rule from CLAUDE.md §4? Include the rule number and text.>

## How was this detected

- [ ] CI invariant test failed: <test name>
- [ ] Production invariant violation: <invariant name and severity>
- [ ] Manual code review
- [ ] External audit / security report
- [ ] Other:

## What's the violation

<Specific description. Include log excerpts, test output, SQL snippets as applicable.>

## Impact

- Personas affected:
- Data at risk:
- Is the engine currently halted?

## Reproduction

<Steps to reproduce if applicable. For detected-in-production: what conditions led to detection?>

## Proposed resolution

- [ ] Root cause known: <describe>
- [ ] Fix proposed: <link to PR or describe approach>
- [ ] Invariant test to add: <describe>
- [ ] ADR needed to clarify the rule: <describe if so>

## Timeline

Priority: P0 (critical), P1 (fix within 48h), P2 (fix within 1 week).

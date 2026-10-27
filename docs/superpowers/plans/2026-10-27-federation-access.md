# Federation contributor access

**Goal:** Remove the contributor identity and evaluation-scope barriers while
keeping external verifier implementations outside this repository.

**Architecture:** Keep the existing offline contracts and sandbox. Accept
external commit authors without permitting reserved owner-identity mismatches.
Allow an edge to name the sealed fixture sets its maintainer agreed to test.
Use the existing container-image entry point for externally owned verifiers.

**Constraints:** Preserve sealed fixture bytes, archive refs, existing records,
claim ceilings, read-only CI permissions and all actuator boundaries. Do not
import third-party implementations, change licences, enroll participants, or
rewrite a contributor's branch. A pull request is the reviewable deliverable.

**Review focus:** External authors; mismatched reserved owner identities;
empty/unknown/path-like fixture selections; existing edges without selections;
failures hidden by shell command substitution.

## Task 1: Contributor authors

- [x] Add real-commit regression cases accepting external names and noreply addresses.
- [x] Watch those cases fail against the workflow's current owner-only check.
- [x] Accept external names while retaining the existing reserved owner-name/email binding and trailer rule.
- [x] Run `tests/unit/test_ci_execution.py`.

## Task 2: Bounded fixture selection

- [x] Add tests for an explicit v1-only selection, legacy selection of every sealed set, and invalid selections.
- [x] Add optional `fixture_sets` to the manifest schema and validator, and a CLI that lists the validated selection.
- [x] Make federation CI execute changed effect-evidence edges only on their declared sets; continue checking every sealed set with the reference verifier.
- [x] Exercise the workflow shell with a real local wrapper, including an invalid selection that must fail before execution.

## Task 3: Contribution instructions and verification

- [x] Document digest-pinned external images, report-only submissions, explicit fixture versions and authentic contributor authorship.
- [x] Correct README's v1/v2 onboarding summary; preserve old results and agreed pins.
- [x] Run the full test suite, manifest/provenance checks and review the final diff.
- [ ] Push an isolated branch and open a PR; report any remaining contributor-owned work on PR #22.

## Verification

- RED: both added external-author cases failed against the owner-only workflow.
- RED: explicit fixture selection and the CI-shell regressions failed before implementation.
- GREEN: targeted workflow/scope tests: 26 passed.
- GREEN: full suite: 386 passed; existing records, results table and v2 provenance checks passed.
- Environment: the initial full run had six localhost integration setup errors because the host injects a SOCKS proxy without socksio. Removing proxy variables only for the local test process produced 6/6 integration passes and the green full suite. No dependency or source change was needed.
- Docker is unavailable locally; sandbox execution must be verified by PR CI.

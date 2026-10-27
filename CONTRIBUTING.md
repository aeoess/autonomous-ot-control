# Contributing

You can contribute one bounded test report without joining a federation,
adopting a framework, sharing credentials, or accepting maintenance work.
Use the [federation guide](docs/federation/README.md) for the exact boundary,
inputs and claim ceilings.

## Authorship and review

Keep your own Git author name and email, including GitHub's private noreply
address. Do not rewrite commits as the repository owner. CI accepts external
authors; its reserved owner-name/email check only detects inconsistent
metadata and does not authenticate an author. The existing ban on
`Co-authored-by` and `Claude-Session` trailers remains in effect.

Submit a pull request. Maintainer review is required before integration;
a contribution grants no control-system access or approval authority.
Fork workflows use a read-only token and no application credentials.

## Independent projects

The owner has explicitly accepted the upstream maintainer's pinned reader in
the isolated `agent-evidence-vectors-axes` edge (PR #22). Its upstream source,
Apache-2.0 licence and local integration changes are documented in that
edge's `run.md`. This exception does not import it into REMORA or the protected
runtime and does not grant blanket approval for other third-party code.

Keep third-party verifier source, libraries, tests and implementation-specific
assets in their owner's repository. Do not vendor them here or add dependency
updates that import them into this project. An independently written protocol
consumer may be reviewed separately with sources and provenance stated.

For a repeatable external checker, submit metadata naming a public container
image pinned by its full `sha256` digest. Its implementation stays external;
the existing runner executes the image without network access, credentials
or a plant route. For a one-off evaluation, submit a report and reproduction
references instead. See the two submission paths in the federation guide.

Pin the testbed SHA, fixture version and package digest. An agreed v1
evaluation remains a v1 evaluation when v2 appears. Expanding the scope
requires a separate opt-in change; never relabel earlier results.

This repository's licence decision is still open. These instructions do not
grant a new licence or change the rights in REMORA or another project.

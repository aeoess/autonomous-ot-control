# aps: APS delegation verifier

The verifier is external. This directory only pins and calls it.

- Source: https://github.com/agent-passport-system/aps-ot-authority at `19fa02a` (Apache-2.0). The image is built from `image/Dockerfile`: `edge/aps/verify.mjs` bundled with agent-passport-system 8.0.1, on `node:24-slim` pinned by digest, run as user 65534.
- Image: `ghcr.io/agent-passport-system/aps-ot-authority-verifier@sha256:edbb29f9c509f2b198899881f14d1d602c5f0d18ca8df7d7305a85a3b916a092`
- Submission: `../../submissions/aps-1/`, minted at `853a274`.
- Author-produced run, in the source repository's CI (run 40737250466): this testbed at `b1e9f0a`, image pulled anonymously by digest, harness in its Docker sandbox without `--trust-edge`. Delegation check 5/5, work-order check 5/5, oracle 5/5, admitted 1.

The verifier decides only the delegation side of the contract. Keys are test-only and derived from public labels. Revocation is not exercised. A pass never authorizes a control action.

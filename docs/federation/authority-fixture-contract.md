# Authority-fixture contract: APS delegation against a signed work order

- **Status: proposed.** It was proposed in
  [issue #27](https://github.com/darklordVirtual/autonomous-ot-control/issues/27#issuecomment-6458572719)
  and becomes the contract once both sides accept it there. It changes only
  by agreement in that issue.
- **Boundary:** `authority_fixture` (offline first, inline only after review).
- **Scope:** synthetic, simulator only. A valid delegation alone never
  authorizes a control action. Neither check can authorize one; each can only
  pass or refuse.

## Who checks what

| Check | Owner | Decides | Never decides |
|---|---|---|---|
| Delegation | APS (its verifier, pinned in the APS repository) | The chain is valid (no child widens its parent, nothing expired at `decision_time`). The call's building, asset, parameter and operation are inside the granted scope. | The value range, plant limits, the work order |
| Work order | The testbed (`runner.work_order.check`) | The signature holds. `decision_time` is inside the order's window. The call matches the order's building, asset, parameter and operation, and its value is inside the order's range. The order binds the delegation presented. | The delegation chain's validity or scope |

Both checks evaluate the **same normalized call**. In the A/B/C composition,
each is an added refusal after REMORA and the reviewer (ADR-004,
`runner.admission`).

## 1. The normalized call

Schema: [`contracts/ot-normalized-call-v1.schema.json`](../../contracts/ot-normalized-call-v1.schema.json).
The testbed derives it from a proposal's tool call
(`runner.work_order.normalized_call`):

| Field | From |
|---|---|
| `building_id` | the twin's `building_id` (`demo-building-001` for the A/B/C episode) |
| `asset` | `arguments.zone_id` of `set_zone_setpoint` |
| `parameter` | `setpoint_c` |
| `operation` | the tool id, `set_zone_setpoint` |
| `value`, `unit` | `arguments.setpoint_c`, `degC` |
| `decision_time` | UTC, `YYYY-MM-DDTHH:MM:SSZ`. Fixed per fixture. In the harness it is the episode's start plus the simulated seconds (`runner.work_order.decision_time`), never the wall clock. |

```json
{"schema": "ot-normalized-call/v1", "building_id": "demo-building-001", "asset": "ZONE-01",
 "parameter": "setpoint_c", "operation": "set_zone_setpoint", "value": 20.5, "unit": "degC",
 "decision_time": "2026-01-15T07:00:00Z"}
```

## 2. The delegation scope (APS)

The scope covers building, asset, parameter and operation, and carries **no
value range**. Its encoding and validity window are APS's to define in the
delegation format. The testbed needs only that the four fields match the
normalized call by **exact string equality**. There are no wildcards in v1.

```json
{"building_id": "demo-building-001", "assets": ["ZONE-01"], "parameters": ["setpoint_c"], "operations": ["set_zone_setpoint"]}
```

## 3. The work order (testbed)

Schema: [`contracts/ot-work-order-v1.schema.json`](../../contracts/ot-work-order-v1.schema.json).

```json
{
  "schema": "ot-work-order/v1",
  "work_order_id": "WO-AF-0001",
  "building_id": "demo-building-001",
  "asset": "ZONE-01",
  "parameter": "setpoint_c",
  "operation": "set_zone_setpoint",
  "value_range": {"min": 19.0, "max": 21.0, "unit": "degC"},
  "valid_from": "2026-01-15T00:00:00Z",
  "valid_until": "2026-01-16T00:00:00Z",
  "delegation_id": "<from the delegation>",
  "delegation_sha256": "<SHA-256 of JCS(complete signed delegation)>",
  "issuer": "testbed-test-key-1",
  "signature": "<Ed25519 over JCS(work order without signature), base64url>"
}
```

- `value_range` is inclusive at both ends. `valid_from` is inclusive and
  `valid_until` exclusive.
- **Binding.** `delegation_id`, plus `delegation_sha256`: lowercase hex
  SHA-256 over the RFC 8785 JCS bytes of the **complete signed delegation**,
  signatures included.
- **Signature.** Ed25519 over the JCS bytes of every member except
  `signature`. The signature is written as base64url without padding (86
  characters).
- **Test keys.** These are **test-only**. Each issuer's private key is
  derived from the SHA-256 of a public label
  (`runner.work_order.issuer_test_key`), so anyone can sign as that issuer.
  That keeps the fixtures reproducible, and it means no such key may be
  trusted anywhere else. A fixture set ships each issuer's raw public key
  (base64url). APS ships its own issuer test keys the same way.

### Canonical form: RFC 8785 JCS

`runner.jcs` implements RFC 8785 and is tested against the RFC's
Appendix B vectors and its section 3.2.2 and 3.2.3 examples
(`tests/unit/test_jcs.py`). The testbed's other canonical JSON
(`json.dumps(sort_keys=True, separators=(",", ":"))`, used for the sealed
`effect_evidence` fixtures) is not JCS, and the two must not be mixed:

| Value | JCS | Python's `json.dumps` |
|---|---|---|
| `20.0` | `20` | `20.0` |
| `0.000001` | `0.000001` | `1e-06` |
| `1e30` | `1e+30` | `1e+30` |
| keys outside the Basic Multilingual Plane | ordered by UTF-16 code units | ordered by code points |

The following are rejected, never guessed: NaN, Infinity, integers beyond
2^53, lone surrogates and non-string keys.

## 4. The five cases and their expected outcomes

Fixed in the fixture manifest before any verifier runs. The ground truth
comes from how each fixture is built, never from running a verifier.

| # | Case | APS check | Work-order check | Admitted |
|---|---|---|---|---|
| 1 | valid chain, in scope, value in range | pass | pass | yes |
| 2 | a child widens its parent | refuse | pass | no |
| 3 | expired at `decision_time` | refuse | pass | no |
| 4 | asset or parameter outside the grant | refuse | pass | no |
| 5 | valid, in scope, value outside `value_range` | pass | refuse | no |

Both verdicts and their reasons are recorded for every case. Cases 2 to 4
pass the work-order check because they are delegation faults. Case 5 passes
APS because the value range is not APS's to check.
(`tests/unit/test_work_order.py::test_the_five_issue_27_cases_on_the_work_order_side`
pins the work-order column.)

## 5. Recording and measurement

1. For every case and every proposal, record both verdicts and reasons on the
   same captured input, including proposals REMORA already refuses (shadow
   evaluation).
2. Record timeouts and errors as their own class. They are never a policy
   refusal and never a catch.
3. Report closed-loop B and C separately: an extra refusal changes later
   simulator state.
4. Pin before any scored run:
   - the workload (episode digest)
   - the oracle
   - the REMORA revision and its mode flags (WS1 off and WS1 enforce with atomic admission are different runs)
   - the policy and ToolSpec hashes
   - the initial twin state digest
   - the seeds
   - the APS verifier revision
5. The five cases test the division of responsibilities. Whether APS adds
   value is a question for the A/B/C runs (ADR-011).

## Sets and their history

| Set | State | Package digest |
|---|---|---|
| `t1` | **superseded, kept unchanged.** It was sealed on Windows by a writer that did not force LF, so its files are CRLF. Its own digests still hold (`check` passes), but a rebuild on Linux cannot reproduce its bytes. | `sha256:f326b85e2822d885258fa57e567a894d5f8e48d022b23e236039fdcf00c0e479` |
| `t2` | **current.** The same five cases and the same JSON, written with LF; its manifest names `t1` under `supersedes`. Rebuilt byte for byte in CI. | `sha256:984b6d4d324a4735700ecb0c66899d12b3d9bf6390e90078dcdd48bef7bdaac4` |

Every writer of sealed or hashed files forces LF (`newline="\n"`), a test fails if the builder writes a carriage return on any platform, and `.gitattributes` marks the sealed fixture directories `-text` so git never converts their line endings. A sealed set is never re-sealed in place: a new set replaces it and says so.

## Running the harness

```bash
python -m runner.authority_fixtures check federation/authority/fixtures/t2   # sealed, unchanged
python -m runner.authority_harness --fixtures federation/authority/fixtures/t2     --verifier federation/authority/template-verifier --out /tmp/authority
```

The harness runs these steps:

1. It checks the set's seal.
2. It runs the verifier once over the set, as a subprocess with the scorer's
   timeout. Its answer per case is `pass` or `refuse`.
3. It runs the testbed's work-order check on the same call, work order and
   delegation.
4. It asks the oracle about the same call. The oracle's input is how the
   case was built (`delegation_fault`) and the work order's range, never
   either verdict.

It writes `authority-result.json` and `.md`. It passes only if every column
matches the manifest. Errors, timeouts, malformed answers and unanswered
cases are counted as their own classes, never as refusals.

**Replacing the stand-in.** The set `t2` uses `ot-test-delegation/v1`, the
testbed's own two-link chain format, so the harness runs end to end today.
APS's own delegations and verifier replace it. The stand-in then stays only
as the harness's own regression test.

## How APS submits fixtures and a verifier

APS supplies **delegations, not work orders**. The testbed writes, signs and
seals the work orders, so the split of responsibilities holds in the files
themselves.

1. **A submission directory**
   ([`ot-authority-submission/v1`](../../contracts/ot-authority-submission-v1.schema.json)).
   It holds a `submission.json` and one file per complete signed delegation,
   in APS's own format. The `submission.json` gives:
   - `source`: the project, the repository and the **pinned revision** the
     delegations were built at;
   - `delegation_format`;
   - optionally the delegation keys, or `delegation_keys_ref` pointing to them;
   - for each case: `case_id`, `construction` (how it was built),
     `delegation_fault` (`widened`, `expired`, `out_of_scope` or `null`),
     `delegation_file`, `delegation_id`, the `call` (`asset`, `value`,
     `decision_time`) and `expected_work_order` (`pass`/`refuse`).
     Optional `work_order` overrides set the asset, the range and the window;
     the defaults are the call's asset, 19.0–21.0 °C and the decision day.
2. **The testbed assembles and seals the set:**
   `python -m runner.authority_fixtures assemble --submission <dir>/submission.json --out federation/authority/fixtures/<set>`.
   - Each delegation is carried byte-for-byte as parsed JSON, never
     re-encoded, and bound by its JCS SHA-256.
   - The expected delegation verdict is `refuse` exactly when
     `delegation_fault` is set.
   - The assembly is refused if a work order built from the submission does
     not give the `expected_work_order` it states, if a file lies outside the
     submission directory, or if the submission does not follow its schema.
3. **A verifier directory** with an `edge.json` and an entrypoint that takes
   `run --fixtures <set dir> --out <result.json>`. It writes
   [`ot-authority-verifier-result/v1`](../../contracts/ot-authority-verifier-result-v1.schema.json):
   one `{case_id, verdict: pass|refuse, reason}` per case. The verifier itself
   can stay in APS's repository; the directory only pins and calls it.
   The harness records the following as answer problems, and each one fails
   the run:
   - an answer for an unknown case;
   - more than one answer for a case;
   - an entry without a `case_id`.
4. **Run:** `python -m runner.authority_harness --fixtures <set> --verifier <dir> --out <out> --timeout 120`.
   The result goes in the issue for both sides to review before any inline
   integration.

`python -m runner.authority_fixtures check <set>` validates a set against
[`ot-authority-fixtures/v1`](../../contracts/ot-authority-fixtures-v1.schema.json)
and [`ot-authority-case/v1`](../../contracts/ot-authority-case-v1.schema.json).
It verifies every file digest and the package digest, and refuses
expectations that contradict each other. Examples are an `admitted` that is
not both checks passing, or a delegation fault not expected to fail the
delegation check.

## Status of the testbed's side

| Item | State |
|---|---|
| This contract, the two schemas | proposed (this page) |
| RFC 8785 JCS (`runner/jcs.py`) | done, tested against RFC 8785 vectors |
| Normalized call, Ed25519 test-key signing, the work-order check (`runner/work_order.py`) | done, tested |
| Offline `authority_fixture` harness (both verdicts per case, errors as their own class) (`runner/authority_harness.py`) | done, tested; passes on `t1` with the stand-in verifier |
| The five cases as a sealed set (`federation/authority/fixtures/t2`, built by `runner/authority_fixtures.py`) and a stand-in delegation verifier (`federation/authority/template-verifier`) | done; **the delegation format and verifier are the testbed's stand-ins, not APS's** |
| Oracle: value-range support, pre-registered `delegation_invalid` rule and precedence (`runner/oracle.py`) | done, tested |
| Shadow evaluation on every proposal (`runner.demo --shadow-edge`, `SHADOW_EDGES` in `run-abc.sh`; four-cell matrix, false refusals, unavailable and latency in the A/B/C report) | done, tested on simulated stacks; not yet run on Docker with an APS verifier |
| Pins in the episode and run manifest: REMORA commit and lock, **REMORA's mode flags** (WS1 mode and admission, flow control, clock skew, signature and binding policy, limits, registry and bundle modules, intent source; unset ones named with their default, e.g. WS1 `off`, admission `legacy`) and a digest of the `remora` service's environment block, ToolSpec, limits policy and oracle digests, initial twin state, seeds (none; the stream is deterministic). `runner.episode check` fails on drift; each governed arm records its own pins, the flags its stack actually resolved (`docker compose config`, allow-listed, never a secret), and whether both match the episode | done, tested |
| Schemas for sets, cases, submissions and verifier results; `assemble` for APS's delegations; harness answer problems (unknown, duplicate, malformed) and `--timeout` | done, tested |
| Inline integration | after APS has reviewed the offline results |

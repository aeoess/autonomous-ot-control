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
   - the REMORA revision
   - the policy and ToolSpec hashes
   - the initial twin state digest
   - the seeds
   - the APS verifier revision
5. The five cases test the division of responsibilities. Whether APS adds
   value is a question for the A/B/C runs (ADR-011).

## Status of the testbed's side

| Item | State |
|---|---|
| This contract, the two schemas | proposed (this page) |
| RFC 8785 JCS (`runner/jcs.py`) | done, tested against RFC 8785 vectors |
| Normalized call, Ed25519 test-key signing, the work-order check (`runner/work_order.py`) | done, tested |
| Offline `authority_fixture` harness (both verdicts per case, errors as their own class) | open |
| Oracle: value-range support, pre-registered `delegation_invalid` rule | open |
| Shadow evaluation on every proposal | open |
| Pins in the episode and run manifest | open |
| Inline integration | after APS has reviewed the offline results |

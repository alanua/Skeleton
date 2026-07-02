# Aufmass Memory

Aufmass uses the active local-private memory stack. Set:

```bash
export SKELETON_PRIVATE_MEMORY_ROOT="$HOME/.local/share/skeleton-private-memory"
python3 scripts/skeleton_private_memory.py init
```

The canonical authority is only:

```text
$SKELETON_PRIVATE_MEMORY_ROOT/canonical.sqlite
```

Graphify and MemPalace are derived indexes rebuilt by `PrivateMemoryStack` after each successful canonical mutation. New Aufmass operations do not create or write the legacy `memory/canonical.sqlite` path.

## Calculation Boundary

`core.aufmass_engine.calculate_aufmass` and the explicit `skeleton.aufmass.local_input.v1` packet remain the only numeric calculation inputs. Memory context is read-only unless `--write-memory` is present. Memory may report prior decisions, source evidence refs, previous calculations, repeated warnings, unresolved blockers and operator-approved project rules. It must not fill missing dimensions, alter geometry/openings/quantities, or promote estimates to confirmed measurements.

## Commands

```bash
python3 scripts/skeleton_local_ops.py aufmass calculate --input input.json --output-dir result
python3 scripts/skeleton_local_ops.py aufmass calculate --input input.json --output-dir result --use-memory
python3 scripts/skeleton_local_ops.py aufmass calculate --input input.json --output-dir result --use-memory --write-memory --actor operator --reason operator_review --approval operator_approved --transaction tx-001
python3 scripts/skeleton_local_ops.py aufmass memory-context --project-ref project-001
python3 scripts/skeleton_local_ops.py aufmass history --project-ref project-001
python3 scripts/skeleton_local_ops.py aufmass compare --input input.json
python3 scripts/skeleton_local_ops.py aufmass review-decision --project-ref project-001 --decision-ref decision-001 --decision-status operator_approved --note "accepted reviewed packet" --actor operator --reason review --approval operator_approved --transaction decision-tx-001
```

`--write-memory` requires actor, reason, approval and transaction metadata. Idempotency is based on `project_ref`, normalized input hash and transaction id.

## Stored Records

Successful memory writes store local-private records for project profile, source evidence refs, normalized input, calculation summary, room results, warnings/blockers, review status, output hashes and operator decisions. Records include explicit relationships so Graphify can connect:

```text
project -> source/evidence -> room -> calculation -> output -> review/decision
```

Records also include bounded searchable text so MemPalace can find prior decisions, warnings, notes and similar past cases. Statuses such as `confirmed`, `estimated_review`, `accepted_input`, `blocked` and `operator_approved` are preserved and not collapsed.

Public tests use synthetic fixtures and aggregate counts only. Real project inputs, drawings, paths, quantities, customer details, calculation records, decisions, indexes and backups remain local-private on the operator server.

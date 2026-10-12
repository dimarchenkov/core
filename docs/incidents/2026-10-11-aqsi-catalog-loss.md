# AQSI catalog loss — 2026-10-11

## Outcome

The current Core code did not delete or archive the AQSI catalog. The repository and its complete
Git history contain no AQSI goods/category delete call. The adapter only reads, creates, updates,
sets shop prices, and verifies the result.

At 2026-10-11 16:19 MSK, read-only production diagnostics established:

- Core had 99 active Variants and 92 active AQSI Publication records;
- all 92 Publication records were `published`;
- the last successful verified publication completed at 2026-10-08 18:04:20 MSK;
- AQSI returned not-found for all 92 stable Core external IDs;
- the deterministic `core-default-goods` category was also absent;
- the AQSI account and its single active shop were still reachable.

This is an account-wide catalog deletion/reset, not only a lost shop binding or a hidden category.
The evidence excludes the current Core integration as the initiator. It cannot distinguish a manual
bulk delete, a request made with the same API credential by another client, or an AQSI-side reset.

## Available evidence

Core publication attempts are append-only and show 93 successful creates, 4 successful updates,
and no delete operation type. Current container logs show periodic synchronization sweeps and
several Redis reconnects, but no destructive AQSI request. The reconnects only interrupt scheduling;
they cannot remove provider data.

AQSI's published API exposes `DELETE /v2/Goods/{goodId}` and
`DELETE /v2/GoodsCategory/{categoryId}`. It does not publish an audit/history endpoint that identifies
who invoked a deletion or when it was processed. AQSI's UI also supports multi-select bulk deletion.

For final attribution, ask AQSI support for server-side audit/data-bus records for the account between
2026-10-08 18:04 MSK and 2026-10-11 16:19 MSK. Include the category ID
`core-default-goods`, several missing Variant UUIDs from Core's reconciliation diagnostics, the API
integration name, source IP/user/session where available, and whether the deletion originated in the
personal account, a cash register, external API, or an AQSI administrative operation.

Official references:

- <https://api.aqsi.ru/> — Goods and GoodsCategory read/create/update/delete contracts;
- <https://aqsi.ru/support/udalenie-tovarov/> — manual and multi-select deletion in the AQSI UI;
- <https://aqsi.ru/support/integraciya-po-vneshnemu-api/> — API-key integration setup.

## Remediation implemented

The five-minute job now performs remote reconciliation even when Core has no local changes:

1. Build the complete expected ready Variant projection from Core.
2. Read every page of AQSI account-wide goods.
3. Compare stable external-ID sets while keeping unrelated AQSI goods out of Core ownership.
4. Read each present Core-managed good and verify all Core-owned fields and shop binding.
5. Queue a create repair for missing goods and an update repair for mismatched goods.
6. Verify every repair through the existing publication processor.

Each comparison persists counts, bounded ID diagnostics, trigger, timestamps, queue results, and
operator-visible status (`healthy`, `repairing`, `needs_attention`, or `failed`). Settings now displays
catalog health independently from API connectivity.

Remote deletion remains unavailable. A good that exists in AQSI but is no longer expected by Core is
reported as a deletion candidate and left untouched. More than five candidates, more than ten percent
of the expected catalog, or any candidates while the expected catalog is empty triggers the explicit
mass-deletion guard. No automatic path can execute a Goods or GoodsCategory delete.

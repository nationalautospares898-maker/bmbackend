# Offline Synchronisation — `bmbackend`

| Field | Value |
|---|---|
| Status | **Phase 1 proposal — not implemented** |
| Owner of this contract | Backend (`sync` module). The client implementation lives in `../../bmfrontend/docs/API-INTEGRATION.md`. |
| Last updated | 2026-10-29 |

---

## 1. The honest version of "offline capability"

The owner asked for cloud-first with offline capability. Taken literally, "the shop can keep selling
with no internet" conflicts with two other confirmed requirements: **no overselling** (C-3) and
**auditable, server-numbered invoices** (C-7, C-9). Two disconnected counters cannot both check the
same last unit of stock, and neither can safely mint an invoice number.

So the scope is drawn deliberately:

| Capability | Offline? | Why |
|---|---|---|
| Open the app, see the billing counter | ✅ | App shell cached |
| Search the cached product subset and see last-known prices | ✅ | Read-only, clearly labelled stale |
| See last-known stock figures | ✅ **advisory only** | Labelled "as of <time>" (FR-OFFLINE-009) |
| Create, edit, switch and delete draft bills | ✅ | Drafts have no financial or stock effect |
| Create a new customer record | ✅ | Client-generated UUID makes it idempotent |
| Print an un-numbered counter slip | ✅ | Clearly watermarked, not an invoice |
| **Finalise a CASH sale (`Print Bill`)** | ❌ | Needs authoritative stock + server invoice number |
| **Finalise a CREDIT sale (`Credit Bill`)** | ❌ | **Also finalises a sale** (FR-CREDIT-020). Reduces stock and allocates the internal reference — identical constraints to a cash sale |
| **Record a payment** | ❌ | Financial record; must not be guessed |
| **Confirm a purchase receipt** | ❌ | Changes stock |
| **Create a return / adjustment** | ❌ | Changes stock and money |
| **Apply an import** | ❌ | Changes catalog and stock |

This is `FR-OFFLINE-001` and `FR-OFFLINE-002`. It is **not** a limitation to be quietly worked
around later: the sync API has no operation type for finalisation, and adding one requires the
design work in §11 plus owner approval (ADR-011).

The practical consequence for the shop, which staff must be trained on before go-live:

> **During an internet outage you can keep writing bills and they will all be safe. You cannot hand
> the customer a numbered invoice until the connection is back.** You can print a counter slip now
> and the real bill afterwards.

---

## 2. Why this is still worth building

A broadband outage at a busy counter currently means a paper bill that someone re-types later, with
the attendant lost sales and transcription errors. With bounded offline support:

- No work is lost and nothing is re-typed.
- The queue drains automatically; the staff action is "press print again", not "find the paper".
- Stock and numbering stay correct, because the server still decides them.

---

## 3. Protocol overview

```
 ┌──────────────── CLIENT (bmfrontend) ────────────────┐      ┌────── SERVER (bmbackend) ──────┐
 │                                                     │      │                                 │
 │  Dexie / IndexedDB                                  │      │                                 │
 │  ├─ drafts            (authoritative while offline) │      │                                 │
 │  ├─ draft_items                                     │      │                                 │
 │  ├─ outbox            (ordered operation log)       │─────►│ POST /api/v1/sync/operations    │
 │  ├─ catalog_cache     (bounded product subset)      │◄─────│ GET  /api/v1/sync/catalog?since │
 │  └─ meta              (cursors, device, server time)│◄─────│ GET  /api/v1/sync/bootstrap     │
 │                                                     │      │                                 │
 │  pending_finalizations (separate queue, §8)         │─────►│ POST /api/v1/sales:finalize      │
 └─────────────────────────────────────────────────────┘      └─────────────────────────────────┘
```

Two distinct queues, deliberately:

| Queue | Contents | Runs offline? | Semantics |
|---|---|---|---|
| **Outbox** | Draft and customer upserts | Yes — accumulates while offline | At-least-once, idempotent on `op_id` |
| **Pending finalisations** | Sale finalisation attempts that were *started online* and lost their response | Never *created* offline | At-least-once retry of the **same** `Idempotency-Key` |

Merging them would blur the line between "safe to replay later" and "may already have taken the
customer's money", so they stay separate (ADR-010).

### 3.1 Three states that must never be confused

Most offline bugs in systems like this come from treating these as one thing. They are not.

| # | State | How it arises | Server effect so far | Correct handling |
|---|---|---|---|---|
| **1** | **Safe offline operation** | Created while offline: draft upsert/delete, customer upsert | **None** — nothing financial has happened | Queue in `outbox`; replay freely; idempotent on `op_id` |
| **2** | **Lost response** | A finalisation sent **while online**; the server answered but the answer never arrived (timeout, dropped connection, 503 after commit) | **Possibly complete** — an invoice may exist, stock may be decremented, payment may be recorded | Retry the **same `Idempotency-Key`**. The server replays its stored response. **Never** send a new key |
| **3** | **Unconfirmed outcome** | Same as (2), but still unresolved after retries | **Unknown** | Hold in `pending_finalizations`, show the loud not-confirmed UI, stop auto-retry after the stale threshold, require a human to check Daily Bills |

**Hard invariants — these are what keep (1) from contaminating (2) and (3):**

1. **`pending_finalizations` can only be created while online.** The row is written immediately
   *before* the first `POST /sales:finalize` attempt, which by definition requires connectivity. No
   code path may add to it from an offline action (FR-OFFLINE-002).
2. **An offline `Print Bill` press creates nothing.** It is refused with the documented message. It
   does not mint an idempotency key, does not write a `pending_finalizations` row, and does not
   enqueue anything. The bill simply remains an editable draft.
2a. **An offline `Credit Bill` press creates nothing either.** Since the owner's 2026-10-29
   change this action **finalises a sale** (FR-CREDIT-020), so it is subject to exactly the same
   offline refusal as `Print Bill`. It does **not** create a credit invoice, does **not** reduce
   stock, does **not** allocate an invoice number, and is **never** queued as a sale for later
   finalisation. The bill remains an editable draft and the cashier is told to finalise it when the
   connection returns. **A credit sale is not "safer" to queue than a cash sale** — both commit
   stock, and neither may be guessed offline.
2b. **Recording a payment is never available offline** and never enters the sync queue
   (FR-OFFLINE-002). Payment collection is a financial event requiring server validation, allocation
   against a real outstanding balance, and duplicate protection.
3. **`pending_finalizations` is never drained by the sync batch endpoint.** It retries
   `POST /sales:finalize` directly. `POST /sync/operations` has no finalisation operation type, so
   there is no route by which a sale could enter the sync queue.
4. **State 2/3 is never permission to create a second sale.** The only safe action is retrying the
   original key. If the user has edited the bill, the old attempt must first be resolved — a new key
   for an edited bill is a *different* sale, and the UI must not allow that while one is unconfirmed.
5. **An unconfirmed sale is never printed as an invoice** — the counter slip is the only permitted
   output (FR-OFFLINE-015).

A structural test asserts invariants 1 and 3 by inspecting the operation registry and the
`pending_finalizations` write sites (AC-OFF-8, `../../bmfrontend/docs/TESTING-STRATEGY.md §8`).

### 3.2 Approval status of this scope

**The owner has not approved this offline scope.** It is recorded as
[OD-8](OWNER-DECISIONS.md#od-8) in the owner decision register and as ADR-011 (status: `proposed`,
owner approval required). It is the **recommended version-one architecture** because it is the only
option that preserves all of C-1, C-3 and C-7 simultaneously — but the owner must consciously accept
that *offline draft safety is not offline billing*, and staff must be trained on it before go-live.

---

## 4. Operation model

### 4.1 Operation envelope

```json
{
  "op_id": "01JBQ8…",              // client-generated UUID, the idempotency unit
  "client_seq": 1041,               // monotonic per device; defines intra-entity order
  "type": "draft.upsert",
  "entity_type": "draft_bill",
  "entity_id": "01JBQ7…",           // the draft/customer UUID
  "base_version": 3,                // last version the client saw; null for a create
  "client_created_at": "2026-10-29T11:02:00+05:30",
  "payload": { }
}
```

### 4.2 Supported operation types (complete for v1)

| Type | Entity | Dispatches to |
|---|---|---|
| `draft.upsert` | `draft_bill` | `SalesService.upsert_draft` — the same method `PUT /drafts/{id}` uses |
| `draft.delete` | `draft_bill` | `SalesService.cancel_draft` |
| `customer.upsert` | `customer` | `CustomersService.upsert` |

**That is the entire list.** The sync module contains no business logic of its own: it deduplicates,
orders and dispatches, so there is exactly one code path per operation whether it arrived online or
offline (ARCHITECTURE §9). An offline draft is not a second kind of draft.

### 4.3 Batching rules

- Max 100 operations per request → `413`/`SYNC_BATCH_TOO_LARGE` beyond that.
- Operations are applied in `client_seq` order **within an `entity_id`**.
- Different entities are independent: one bad draft never blocks another draft or a customer.
- On the first non-`accepted` result for an entity, that entity's remaining operations in the batch
  are returned as `rejected` with `code: SKIPPED_AFTER_EARLIER_FAILURE` and are retried after the
  user resolves the conflict. Order within an entity is never violated.

### 4.4 Per-operation outcomes (FR-OFFLINE-005)

| Status | Meaning | Client action |
|---|---|---|
| `accepted` | Applied; `server_state` returned | Replace local copy, drop from outbox |
| `replayed` | Already applied earlier (same `op_id`); original result returned | Drop from outbox — **not an error** |
| `conflicted` | Rejected on a version check; `server_state` included | Show the user a keep-mine / keep-theirs choice |
| `rejected` | Permanently invalid (validation, permission, revoked device, deleted entity) | Surface with the reason; move to a visible "needs attention" list |

There is no silent discard and no silent merge. A rejected operation stays visible to the user until
they act on it (FR-OFFLINE-005/006).

### 4.5 Idempotency and ordering

- `sync_operations.id = op_id` is the primary key, so a replayed `op_id` is caught by a unique
  violation and answered from the stored result (FR-OFFLINE-004).
- `client_seq` is monotonic per device and used only for ordering within an entity — never to order
  financial effects across devices.
- **Server time is authoritative.** `client_created_at` is stored for diagnostics; `server_received_at`
  is what any business logic uses (FR-OFFLINE-007). A counter with a wrong clock cannot backdate a
  record (E-14).

---

## 5. Draft conflict resolution

Drafts are the only synced entity with a meaningful conflict, because they are mutable.

### 5.1 Ownership first

A draft belongs to `(business, branch, counter)`. The common "conflict" case — two counters editing
the same bill — is prevented by ownership rather than resolved by merging: a different counter gets
`403 DRAFT_OWNED_BY_OTHER_COUNTER` and a manager with `billing.draft.takeover` must take it over
explicitly (FR-DRAFT-010). This matches how a shop actually works: a bill belongs to the counter
that is serving the customer.

### 5.2 Version check for the remaining case

The real case is **the same counter on two devices** (a tablet and the counter PC), or one device
that was offline while the other synced.

```
client sends base_version = 3
   server version == 3  →  apply, version becomes 4, status "accepted"
   server version  > 3  →  status "conflicted", server_state attached
   server version  < 3  →  impossible; treated as "rejected" with CORRUPT_VERSION and logged
```

### 5.3 Resolution policy — chosen behaviour

**A conflicted draft is presented to the user with both versions and three choices**: keep mine
(re-send with the server's current version as the new base), keep theirs (discard local, adopt
server), or open both side by side and copy lines across manually.

| Alternative | Why rejected |
|---|---|
| Last-writer-wins silently | Loses billed lines without the user noticing — unacceptable for money |
| Automatic line-level merge | Plausible-looking merges can double a quantity or resurrect a deleted line; a wrong bill is worse than a visible conflict |
| Server-side locking of drafts | A counter that goes offline mid-bill would hold the lock; practically unworkable |

Chosen: **explicit user resolution, no automatic merge** (FR-OFFLINE-006, ADR-007). A conflict is a
rare event (same counter, two devices, concurrent edit) and the cost of asking is one extra click.

### 5.4 Deletion conflicts

- Local delete vs. remote edit → the delete wins only if `base_version` matches; otherwise
  `conflicted` and the user confirms.
- Local edit vs. remote delete → `rejected` with `DRAFT_ALREADY_CANCELLED`; the client offers
  "recreate as a new bill" with a fresh id, so the lines are not lost.
- Local edit vs. remote **finalisation** → `rejected` with `DRAFT_ALREADY_FINALIZED` and the invoice
  attached, so the user sees that the bill was already sold rather than quietly re-billing it.

---

## 6. Catalog cache

### `GET /api/v1/sync/catalog?since=<cursor>&limit=<n>`

```json
{
  "products": [ { "id": "…", "name": "…", "sku": "…", "unit_code": "SET",
                  "default_selling_price": "1250.0000", "is_active": true,
                  "aliases": ["…"], "updated_at": "…" } ],
  "stock": [ { "product_id": "…", "branch_quantity": "4.000" } ],
  "deleted_product_ids": [],
  "next_cursor": "eyJ1cGRhdGVkX2F0Ijoi…",
  "has_more": false,
  "reset_required": false,
  "as_of": "2026-10-29T11:04:08+05:30",
  "cache_policy": { "max_rows": 5000, "ttl_hours": 72 }
}
```

Rules:
- The cursor is `(updated_at, id)`, so the delta is stable under concurrent writes.
- **The cache is a bounded subset, not the catalog** (FR-OFFLINE-008): active products ordered by
  `sales_rank` then recency, capped at `settings.sync.catalog_cache_max_rows` (default 5000). A
  100k-SKU catalog is never shipped to a browser.
- `reset_required: true` when the client's cursor predates the retention window or the cap changed —
  the client clears and re-pulls rather than carrying a half-stale cache.
- `cost_price` is **never** included in the sync payload, regardless of permission. Cost data does
  not belong in an unencrypted browser store (SECURITY §11).
- Stock figures are included but every client surface that shows them must label them with `as_of`
  (FR-OFFLINE-009). They are a hint about whether to walk to the shelf, not a guarantee.

### 6.1 Offline search quality

Offline search runs against the cached subset only. The UI states plainly that results are limited
and from the cached list. A product that exists but is not cached is simply not findable offline —
which is correct and honest, and is why the cap is configurable.

---

## 7. Sync triggers

Relying on the Background Sync API alone is not acceptable: it is unsupported in some browsers and
not guaranteed to run even where it exists (FR-OFFLINE-012). The client attempts sync on **all** of:

1. `online` event.
2. App focus / tab visible.
3. A timer (30 s while there is a pending outbox, 5 min when idle).
4. Explicit user action ("Sync now" in the status area).
5. Immediately after any local mutation while online (debounced ~500 ms) — so in normal operation
   the outbox is nearly always empty and the offline loss window equals the outage itself (E-11).
6. Background Sync, *if available*, as an additional opportunistic trigger only.

Backoff on failure: 1 s, 2 s, 5 s, 15 s, 60 s, then every 5 min, with jitter. `rejected` operations
are not retried automatically — they need a human.

---

## 8. Finalisation during a network failure

This is the sharpest edge in the system (E-1, E-2) and the sequence is fixed:

```
 user presses "Print Bill" (cash) or "Credit Bill" (credit)
        │
        ├─ offline?  →  REFUSED, clearly: "No connection — the bill is saved as a draft.
        │                You can print a counter slip now and the invoice when you're back online."
        │                (no idempotency key is minted, nothing is queued as a sale)
        │
        └─ online → generate Idempotency-Key, store {draft_id, key, payload_hash, started_at}
                    in `pending_finalizations`, THEN send the request
                          │
     ┌────────────────────┼──────────────────────────────────┐
     │ 201 Created        │ 409 business conflict            │ network error / timeout / 503
     │ → clear pending    │ → clear pending, show the        │ → KEEP pending, state =
     │ → print invoice    │   specific resolution UI         │   "NOT YET CONFIRMED"
     └────────────────────┴──────────────────────────────────┴───────────────┬──────────┘
                                                                            │
                        on reconnect / focus / timer: retry the SAME key
                                                                            │
            ┌───────────────────────────────────────────────────────────────┴───┐
            │ 201 + Idempotent-Replay: true → the sale had committed; print now  │
            │ 201 (fresh)                   → it had not; now it has; print now  │
            │ 409 DRAFT_ALREADY_FINALIZED   → show the existing invoice          │
            └────────────────────────────────────────────────────────────────────┘
```

Non-negotiable rules:

1. **The idempotency key is generated once per user intent and reused for every retry.** A new key
   is minted only when the user changes the bill. This is the mechanism that makes "committed but
   the response was lost" safe (E-2).
2. **An unconfirmed sale is never printed as an invoice.** No invoice number exists client-side
   because only the server allocates one. The fallback is a watermarked, un-numbered counter slip.
3. **A pending finalisation is impossible to ignore.** The bill tab shows a distinct
   "Not confirmed" state and the user cannot close the app without an explicit warning.
4. Pending finalisations older than `settings.sync.finalization_stale_minutes` (default 60) stop
   auto-retrying and ask the user to check Daily Bills — because by then a human should decide, not a
   retry loop.
5. If the user force-closes the browser with a pending finalisation, it is still in IndexedDB and is
   picked up on next load.

---

## 9. Device revocation and session expiry

| Event | Server | Client |
|---|---|---|
| Device revoked | Every endpoint, including `/sync/operations`, returns `403 DEVICE_REVOKED`; refresh family invalidated | Stops syncing, shows a blocking message, **retains local data** so nothing is lost if access is restored (FR-OFFLINE-010) |
| Refresh token expired | `401` on refresh | Prompts login; drafts remain local and sync after login |
| User deactivated | `403 USER_INACTIVE` | Same as above |
| Access token expired while offline | n/a | Drafts continue to work; sync is attempted after re-auth |

The client never deletes local drafts in response to an auth error. Deleting a user's unsold bills
because a token expired would be the worst possible failure mode.

---

## 10. Server-side implementation notes

- `sync_operations` is the dedup table (`DATA-MODEL §3.12`), keyed by `op_id`, retained 30 days.
- Each operation is applied in **its own transaction**, so one failure does not roll back the
  accepted ones in the batch.
- The handler is thin: validate envelope → check device/session → dedup → dispatch to the owning
  service → record the result. No business rule lives here.
- Metrics: batch size, operations by status, conflict rate, oldest `client_created_at` in a batch
  (= how long a device was offline), outbox drain latency. A rising conflict rate means the
  ownership model or the UI needs attention.
- A device sending operations with an unexpectedly old `client_created_at` is logged — useful for
  spotting a machine that has been offline for days, or a clock problem.

---

## 11. If offline finalisation is ever required (deferred design)

Not in scope for v1. If the owner later insists that billing must continue through outages, **both**
of these must be designed, approved and implemented first (FR-OFFLINE-013, ADR-011):

1. **Per-counter stock allocation.** Each counter is granted a reserved quantity per product, decided
   while online. Offline sales consume only that allocation, so two counters cannot oversell the same
   unit. Costs: stock is fragmented and partly invisible to other counters; allocations must expire
   and be reconciled; the no-overselling guarantee becomes "no overselling of allocated stock".
2. **Per-counter invoice number blocks.** Each counter pre-allocates a block of numbers (e.g.
   `NAS/2026-27/C1/00401–00450`). Gapless-per-series is then impossible — unused numbers in a block
   are permanent gaps — which may conflict with the statutory numbering answer to Q-TAX-3. This is
   precisely why the owner and the accountant must answer that question before this feature can be
   considered.

Interim honest position: during an outage the shop captures drafts and prints counter slips, then
finalises in a batch when connectivity returns. The invoice date is the server date of finalisation,
not the draft date — a difference the staff must understand, and an owner question if it matters for
their books.

---

## 12. Acceptance criteria

| ID | Criterion |
|---|---|
| AC-6 | With the network disabled: drafts can be created/edited/deleted; on reconnect every operation reports an explicit outcome; finalisation is refused with the documented message |
| AC-OFF-1 | Replaying a whole batch of already-synced operations yields all `replayed` and changes nothing |
| AC-OFF-2 | A draft edited offline on device A and online on device B produces exactly one `conflicted` result with both states, and no data is lost either way |
| AC-OFF-3 | Finalisation whose response is dropped, then retried with the same key, yields one invoice, one set of stock movements, one payment |
| AC-OFF-4 | A revoked device is refused on sync and retains its local data |
| AC-OFF-5 | Browser restart with 20 drafts and a non-empty outbox loses nothing |
| AC-OFF-6 | A `reset_required` catalog response causes a clean cache rebuild with no duplicate rows |
| AC-OFF-7 | An operation batch containing one invalid operation still applies the valid ones for other entities |
| AC-OFF-8 | No sync operation type exists that finalises a sale, records a payment, or moves stock — asserted structurally in a test over the operation registry |

---

## 13. Related documents

`docs/PRD.md` (FR-OFFLINE-*) · `docs/ARCHITECTURE.md §9` · `docs/API-SPECIFICATION.md §3.12` ·
`docs/DATA-MODEL.md §3.12` · `docs/SECURITY.md §11` · `docs/DECISIONS.md` (ADR-007, ADR-010,
ADR-011) · `../../bmfrontend/docs/API-INTEGRATION.md` *(client implementation of this protocol)*

# Traceability Matrix

> **Current Phase 2 design disposition (2026-10-30):** [Design Completion Addendum](PHASE-2-DESIGN-COMPLETION-ADDENDUM-2026-10-30.md) is the current cross-cutting decision record. It resolves superseded open labels and defines the Phase 3 implementation baseline. Historical closure reports/addenda remain unchanged; legal, physical and operational gates remain gates. It does not represent implementation or test completion.


| Field | Value |
|---|---|
| Status | Phase 1 — **this is the single authoritative matrix for both repositories** |
| Referenced by | `../../bmfrontend/docs/DECISIONS.md`, `../../bmfrontend/docs/PRD.md` (link, not copy) |
| Last updated | 2026-10-29 |

Maps **business objective → requirement → frontend behaviour → backend endpoint → database entities
→ tests**. Requirement IDs are shared across both repositories (ADR-021); the same ID describes the
same end-to-end behaviour on both sides.

Test names are the **names tests must be given in Phase 2**. None exist yet.

---

## 1. Column meanings

| Column | Owner | Meaning |
|---|---|---|
| Requirement | shared | ID from `PRD.md` (backend) and/or `../../bmfrontend/docs/PRD.md` |
| Frontend behaviour | `bmfrontend` | Screen/component responsible; spec in `UI-UX-SPECIFICATION.md` |
| Backend endpoint | `bmbackend` | Contract in `API-SPECIFICATION.md` |
| Entities | `bmbackend` | Tables in `DATA-MODEL.md` |
| Tests | both | Backend: `TESTING-STRATEGY.md`. Frontend: `../../bmfrontend/docs/TESTING-STRATEGY.md` |

---

## 2. BO-4 "Faster billing than paper" — the billing counter

| Requirement | Frontend behaviour | Backend endpoint | Entities | Tests |
|---|---|---|---|---|
| FR-CAT-003 search from first character | `ProductSearchBox` — debounced 120 ms, keyboard nav | `GET /products/search` | `products`, `product_aliases`, `inventory_balances` | BE `test_search_first_character`, `test_search_ranking_order`; FE `ProductSearchBox.test.tsx`, E2E `billing-search.spec.ts` |
| FR-CAT-004 results show price + stock | Result row renders price, branch stock, `as_of` | same | `products`, `inventory_balances` | BE `test_search_includes_stock`, `test_cost_price_omitted_without_permission`; FE `test_search_row_renders_stock` |
| FR-CAT-006 separator-tolerant part codes | Raw input passed through unchanged | same | `products.normalized_sku` | BE `test_search_normalization_variants`; FE n/a |
| FR-CAT-005 no full-catalog download | No bulk product fetch in the client | same (paginated) | — | FE structural `test_no_bulk_catalog_fetch`; BE `test_search_limit_clamped` |
| FR-CAT-007 barcode input | Scanner keystrokes routed to search | `GET /products/lookup-barcode` | `products.barcode` | BE `test_barcode_exact_lookup` |
| FR-DRAFT-002 ≥20 bill tabs | `BillTabStrip`, `+` new bill | `GET/PUT /drafts` | `draft_bills`, `counters.max_open_drafts` | BE `test_twenty_drafts_open`, `test_draft_limit_reached_discards_nothing`; FE `test_tab_strip_20_tabs`, E2E `twenty-drafts.spec.ts` |
| FR-DRAFT-003 draft header + lines | `BillHeaderFields`, `BillItemsTable` | `PUT /drafts/{id}` | `draft_bills`, `draft_bill_items` | BE `test_draft_upsert_roundtrip`; FE `BillItemsTable.test.tsx` |
| FR-DRAFT-007 switching never finalises or loses | Tab switch is local state only | — | `draft_bills` | FE E2E `draft-switch-preserves.spec.ts`; BE `test_draft_edit_creates_no_movements` |
| FR-DRAFT-011 client totals are previews | Totals labelled, recomputed on finalise | `PUT /drafts/{id}` → `preview_totals` | — | FE `test_totals_are_preview`; BE `test_client_total_mismatch_rejected` |
| NFR-PERF-001 search p95 ≤ 150 ms | — | `GET /products/search` | search indexes | BE `perf/test_search_100k.py` |
| NFR-PERF-003 draft save p95 ≤ 250 ms | Debounced autosave | `PUT /drafts/{id}` | `draft_bills` | BE `perf/test_draft_upsert.py` |

---

## 3. BO-1/BO-6 "Every sale recorded, auditably" — finalisation

| Requirement | Frontend behaviour | Backend endpoint | Entities | Tests |
|---|---|---|---|---|
| FR-SALE-001 atomic finalisation | `Print Bill` (cash) / `Credit Bill` (credit) → one request | `POST /sales:finalize` | `invoices`, `invoice_items`, `stock_movements`, `payments`, `payment_allocations`, `invoice_series`, `draft_bills` | BE `test_finalize_cash_sale`, `test_finalize_rollback_leaves_nothing`, `test_rollback_consumes_no_number` |
| FR-SALE-002 idempotent finalisation | Key generated once per intent, reused on retry | same + `Idempotency-Key` | `idempotency_keys` | BE `test_finalize_idempotent_replay`, `test_finalize_key_reuse_conflict`, `test_concurrent_same_key_one_invoice`; FE `test_idempotency_key_reused_on_retry` |
| FR-SALE-003 server-authoritative money | Sends `client_computed_total` as a check only | same | `invoices` | BE `test_total_mismatch_rejected`, property `test_money_conservation`; FE `test_never_displays_own_total_as_final` |
| FR-SALE-004 in-transaction **internal** sale reference (unique + sequential) | **Renders no invoice number at all** ([OD-3](OWNER-DECISIONS.md#od-3)) | same | `invoice_series`, `invoices` | BE `test_numbering_unique_sequential_under_20_parallel`, `test_rollback_consumes_no_number`, `test_fy_rollover_creates_series_lazily`; **structural `test_no_invoice_number_in_print_payload`**; FE structural `test_no_invoice_number_rendered`. ⚠️ Gap detection **dropped** — AC-1a withdrawn |
| FR-SALE-005 immutable item snapshots | — | same | `invoice_items` | BE `test_item_snapshot_survives_catalog_edit` |
| FR-SALE-006 price-change detection | `PriceChangedDialog` shows draft vs current | `409 PRICE_CHANGED` | `draft_bill_items.catalog_price_at_add`, `products` | BE `test_price_changed_blocks`, `test_accept_price_changes`; FE `PriceChangedDialog.test.tsx`, E2E `price-changed.spec.ts` |
| FR-SALE-007 insufficient stock | `InsufficientStockDialog` with shortfalls | `409 INSUFFICIENT_STOCK` | `inventory_balances` | BE `test_insufficient_stock_no_side_effects`; FE `InsufficientStockDialog.test.tsx` |
| FR-SALE-008 audited oversell override | Reason field, ≥10 chars, supervisor path | same + `oversell_override` | `stock_movements.oversell_override`, `invoices.oversell_reason`, `audit_logs` | BE `test_oversell_requires_permission`, `test_oversell_requires_reason`, `test_oversell_audited` |
| FR-SALE-009 one engine for cash and credit | Same submit path, `sale_type` differs | same | same | BE `test_cash_and_credit_structurally_identical` |
| FR-SALE-010 invoices immutable | No edit UI anywhere | **no** PUT/PATCH/DELETE route | `invoices`, `invoice_items` | BE `test_no_invoice_mutation_route` (OpenAPI scan), `test_app_role_cannot_update_invoice_items`; FE structural `test_no_invoice_edit_route` |
| FR-SALE-011 cancellation + reversal | `CancelInvoiceDialog`, reason required | `POST /invoices/{id}:cancel` | `invoices`, `stock_movements`, `credit_notes`, `audit_logs` | BE `test_cancel_reverses_stock_and_issues_credit_note`, `test_cancel_window_expired`, `test_double_cancel_conflict` |
| FR-SALE-012 no double finalisation | Submit disabled while in flight | `409 DRAFT_ALREADY_FINALIZED` | `uq_draft_bills__invoice` | BE `test_finalize_twice_conflict`; FE `test_submit_disabled_in_flight` |
| FR-SALE-014 reject empty/invalid bills | Button disabled, inline validation | `422` | — | BE `test_empty_sale_rejected`; FE `test_complete_disabled_when_empty` |
| C-7 / FR-AUDIT-001 auditable history | — | all writes | `audit_logs` | BE `test_every_write_writes_audit_row` |

---

## 4. BO-2 "Know true stock" — inventory

| Requirement | Frontend behaviour | Backend endpoint | Entities | Tests |
|---|---|---|---|---|
| FR-INV-001 balance + ledger | `StockPositionScreen`, `ProductLedgerScreen` | `GET /inventory/balances`, `GET /inventory/movements` | `inventory_balances`, `stock_movements` | BE `test_balance_equals_ledger_sum`, property `test_stock_conservation` |
| FR-INV-003 movements immutable | No edit UI | no mutation route | `stock_movements` | BE `test_app_role_cannot_update_movements` |
| FR-INV-004 no double-spend of last unit | — | `POST /sales:finalize` | `inventory_balances` (row lock) | BE `concurrency/test_two_counters_last_unit`, `test_ten_sales_three_stock` |
| FR-INV-005 negative only via override | — | same | `stock_movements.oversell_override` | BE property `test_no_negative_without_override` |
| FR-INV-006 reconciliation alerts, never auto-fixes | `ReconciliationReportScreen` (read-only) | `GET /inventory/reconciliation/latest` | `inventory_balances`, `stock_movements` | BE `test_reconcile_detects_and_does_not_fix` |
| FR-INV-007 reasoned adjustments | `StockAdjustmentDialog`, reason required | `POST /inventory/adjustments` | `stock_movements`, `audit_logs` | BE `test_adjustment_requires_permission_and_reason` |
| FR-INV-008 low stock | Low-stock list + billing warning | `GET /inventory/balances?low_stock_only=` | `products.reorder_level` | BE `test_low_stock_filter` |
| FR-PUR-003 only receipts add stock | `PurchaseReceiptScreen` → explicit Confirm | `POST /purchases/{id}:confirm` | `purchases`, `purchase_items`, `stock_movements` | BE `test_draft_purchase_no_stock`, `test_confirm_creates_movements` |
| FR-PUR-006 no double receipt | Duplicate warning | `409 DUPLICATE_SUPPLIER_DOCUMENT` | `uq_purchases__supplier_doc` | BE `test_duplicate_supplier_document` |
| NFR-DATA-001 | — | — | both tables | BE `invariants/test_stock_ledger.py` |

---

## 5. BO-3 "Stop credit leakage" — credit and payments

| Requirement | Frontend behaviour | Backend endpoint | Entities | Tests |
|---|---|---|---|---|
| FR-CREDIT-001 credit sale via the same engine | `BillActions` → **one** submit from the billing screen; customer chosen inline in the bill header. **No intermediate form** (OD-21, ADR-033) | `POST /sales:finalize` (`sale_type: credit`) | `invoices.is_credit` | BE `test_credit_sale_appears_in_daily_sales`, `test_credit_finalizes_in_one_request`; FE ST `test_no_credit_details_route` |
| FR-CREDIT-002 derived outstanding | `CreditBillsScreen` shows server values | `GET /credit/outstanding` | `invoices`, `payment_allocations`, `credit_note_allocations` | BE `test_outstanding_derivation_matrix`, `invariants/test_payment_allocation.py` |
| FR-CREDIT-003 no editable paid flag | **No paid checkbox exists** | no route sets it | `invoices.payment_status` | BE `test_no_route_sets_payment_status` (OpenAPI scan); FE structural `test_no_paid_checkbox` |
| FR-CREDIT-004 initial part payment | **Optional and not requested at save time** (FR-CREDIT-025). When paid immediately it is recorded from the confirmation card or Credit Bills as a normal payment | `POST /sales:finalize` with `payment` (optional), else `POST /payments` | `payments`, `payment_allocations` | BE `test_credit_with_part_payment`, `test_credit_without_payment_is_unpaid` |
| FR-CREDIT-005 due dates + ageing | Ageing buckets in `CreditBillsScreen` | `GET /credit/ageing-summary` | `invoices.due_date` | BE `test_ageing_bucket_boundaries` (frozen clock) |
| FR-CREDIT-006 credit limit | Warning or block dialog | `409 CREDIT_LIMIT_EXCEEDED` | `customers.credit_limit` | BE `test_credit_limit_warn_and_block` |
| FR-PAY-001/002 payments + allocations | `RecordPaymentDialog` | `POST /payments` | `payments`, `payment_allocations` | BE `test_allocation_strategies`, `test_over_allocation_rejected` |
| FR-PAY-003 on-account remainder | Shown as customer credit | same | `payments`, `customer_credit_balances` | BE `test_remainder_becomes_on_account` |
| FR-PAY-004 idempotent payments | Key per intent | same + `Idempotency-Key` | `idempotency_keys` | BE `test_duplicate_payment_once`; FE `test_payment_key_reuse` |
| FR-PAY-005 reversal not edit | `ReversePaymentDialog`, reason | `POST /payments/{id}:reverse` | `payments.reverses_payment_id` | BE `test_reversal_leaves_original` |
| FR-CUST-002 credit needs a customer | Form requires selection/creation | `409 CREDIT_CUSTOMER_REQUIRED` | `invoices.customer_id` | BE `test_credit_requires_customer`; FE `test_credit_form_requires_customer` |
| FR-CUST-003 create customer inline | `CustomerQuickCreate`, client UUID | `PUT /customers/{id}` | `customers` | BE `test_customer_upsert_idempotent` |
| FR-CUST-004 duplicate detection, no auto-merge | Candidate list, user chooses | `POST /customers/check-duplicates` | `customer_phones` | BE `test_duplicate_candidates_no_automerge` |
| NFR-DATA-003/004 | — | — | `invoices`, allocations | BE `invariants/test_payment_allocation.py`, `concurrency/test_parallel_allocations` |

---

## 6. C-9 "Printed bill matches the Excel invoice" — printing

| Requirement | Frontend behaviour | Backend endpoint | Entities | Tests |
|---|---|---|---|---|
| FR-PRINT-001 backend owns content | `InvoicePrintLayout` renders `print_payload` verbatim | `GET /invoices/{id}/print-payload` | `invoices`, `invoice_items`, `businesses` | BE `test_print_payload_complete`; FE `InvoicePrintLayout.test.tsx`, snapshot `print-a5.spec.ts` |
| FR-PRINT-002 title follows tax mode | Renders `document_title`; never hardcodes it | same | `settings.tax.mode`, `invoices.document_kind` | BE `test_estimate_only_never_says_tax_invoice`; FE structural `test_no_hardcoded_tax_invoice_string` |
| FR-PRINT-003 reprint is read-only | Reprint button re-renders | same (GET) | — | BE `test_reprint_creates_nothing`; FE E2E `reprint.spec.ts` |
| FR-PRINT-004 duplicate marking + log | `DUPLICATE COPY` watermark | `POST /invoices/{id}/print-events` | `print_log` | BE `test_print_event_logged`; FE `test_duplicate_watermark` |
| C-9 layout columns | **`S R · ITEM · QTY · PRICE · TOTAL`** exactly — transcribed from the owner's reference screenshot, superseding the earlier `S.No/Item/Qty/Price/Total` reconstruction | `print_payload.columns` | `invoice_items` | FE `print-layout.spec.ts`; **manual** go-live check on the real printer ([OD-5](OWNER-DECISIONS.md#od-5)) |
| E-8 printer failure harmless | Invoice shown with Reprint | — | — | FE E2E `print-failure.spec.ts` |

---

## 7. C-1 "Offline capability" — sync

| Requirement | Frontend behaviour | Backend endpoint | Entities | Tests |
|---|---|---|---|---|
| FR-OFFLINE-001 scope: drafts + cached reads | `OfflineBanner`, disabled actions | `POST /sync/operations`, `GET /sync/catalog` | `sync_operations`, `draft_bills`, `customers` | FE E2E `offline-drafts.spec.ts`; BE `sync/test_operation_types` |
| FR-OFFLINE-002 no offline finalisation | **Both** `Print Bill` **and** `Credit Bill` refuse with the documented message, creating nothing | **no sync type exists** | — | BE `test_no_finalize_sync_type` (registry scan); FE E2E `offline-finalize-refused.spec.ts`, `offline-credit-refused.spec.ts` |
| FR-OFFLINE-003 durable outbox | Dexie `outbox` store | `POST /sync/operations` | `sync_operations` | FE `outbox.test.ts`; BE `sync/test_batch_ordering` |
| FR-OFFLINE-004 idempotent on `op_id` | Reuses `op_id` on retry | same | `sync_operations.id` | BE `sync/test_replay_batch_all_replayed` |
| FR-OFFLINE-005 explicit outcomes | `SyncStatusPanel` lists pending/conflicted/rejected | same | `sync_operations.status` | BE `sync/test_per_operation_outcomes`; FE `SyncStatusPanel.test.tsx` |
| FR-OFFLINE-006 user resolves conflicts | `DraftConflictDialog` | `409 DRAFT_VERSION_CONFLICT` | `draft_bills.version` | BE `sync/test_conflict_returns_both_states`; FE `DraftConflictDialog.test.tsx` |
| FR-OFFLINE-007 server time authoritative | Sends `client_created_at` for diagnostics | same | `sync_operations.server_received_at` | BE `sync/test_client_clock_skew_ignored` |
| FR-OFFLINE-008 bounded catalog cache | Dexie `catalog_cache`, capped | `GET /sync/catalog?since=` | `products` | BE `sync/test_catalog_delta_cap_and_reset`; FE `catalogCache.test.ts` |
| FR-OFFLINE-009 stock labelled `as_of` | Every stock figure carries a timestamp | same | — | FE `test_stock_shows_as_of` |
| FR-OFFLINE-010 revoked device | Blocking message, local data retained | `403 DEVICE_REVOKED` | `devices.status` | BE `sync/test_revoked_device_refused`; FE `test_revoked_keeps_local_data` |
| FR-OFFLINE-011 survive restart | IndexedDB persistence | — | — | FE E2E `restart-recovery.spec.ts` |
| FR-OFFLINE-012 multiple sync triggers | online/focus/timer/manual/post-mutation | — | — | FE `syncScheduler.test.ts` |
| E-1/E-2 lost response | `pending_finalizations`, "Not confirmed" state, counter slip | retry same key | `idempotency_keys` | FE E2E `lost-response-retry.spec.ts`; BE `test_finalize_idempotent_replay` |

---

## 8. Security, access and multi-counter

| Requirement | Frontend behaviour | Backend endpoint | Entities | Tests |
|---|---|---|---|---|
| FR-AUTH-001/002 login, rotating refresh | `LoginScreen`, in-memory access token | `POST /auth/login`, `/auth/refresh` | `users`, `refresh_tokens` | BE `test_refresh_rotation_and_reuse_detection`; FE `authClient.test.ts` |
| FR-AUTH-003 scoped context | Permission-aware UI | `GET /auth/me` | `memberships` | BE `test_auth_context_resolution` |
| FR-AUTH-004 device registry + revoke | `DeviceListScreen` | `POST /admin/devices/{id}:revoke` | `devices` | BE `test_revoked_device_blocked_everywhere` |
| FR-AUTH-005 PIN switch | `UserSwitchDialog` | `POST /auth/pin-switch` | `users.pin_hash` | BE `test_pin_switch_rate_limited` |
| FR-AUTH-006 rate limit + lockout | Lockout message with countdown | `429`, `423` | `login_attempts` | BE `test_login_lockout` |
| NFR-SEC-003 token storage | **Never** `localStorage` | — | — | FE structural `test_no_token_in_storage`; BE n/a |
| NFR-SEC-004 server-side authorisation | UI hides, server enforces | every endpoint | `role_permissions` | BE `api/test_permissions.py` (generated per route) |
| NFR-SEC-005 tenant isolation | — | every endpoint | `business_id`/`branch_id` | BE `api/test_isolation.py` (generated matrix, AC-8) |
| NFR-SEC-007 upload safety | File picker with accept + size hint | `POST /imports` | `import_jobs` | BE `api/test_uploads.py` |
| NFR-SEC-012 no secrets in frontend | Only `VITE_API_BASE_URL` | — | — | FE CI secret scan + env allow-list test |
| FR-AUDIT-001…004 | `AuditLogScreen` | `GET /audit-logs` | `audit_logs` | BE `test_audit_append_only`, `test_audit_never_contains_secrets` |
| C-2 multi-counter | Counter selection; drafts are counter-scoped | all billing endpoints | `counters`, `draft_bills` | BE `concurrency/test_multi_counter_sales`; FE E2E `two-counters.spec.ts` |

---

## 9. Imports

| Requirement | Frontend behaviour | Backend endpoint | Entities | Tests |
|---|---|---|---|---|
| FR-IMP-001 upload validation | `ImportUploadScreen`, accept + size | `POST /imports` | `import_jobs` | BE `test_upload_type_size_matrix` |
| FR-IMP-002 explicit intent | Intent selector, supplier required for receipts | same | `import_jobs.intent` | BE `test_price_list_never_touches_stock` |
| FR-IMP-004 duplicate file | Duplicate warning + override reason | `409 DUPLICATE_FILE` | `uq_import_jobs__file` | BE `test_duplicate_file_detected` (E-9) |
| FR-IMP-005 state machine | Status chip with progress | `GET /imports/{id}` | `import_jobs.status`, `jobs` | BE `test_import_state_machine_transitions` |
| FR-IMP-006/007 per-row review | `ImportReviewTable`, row decisions | `PATCH /imports/{id}/rows/{row_id}` | `import_rows.match_status`, `.decision` | BE `test_row_classification_matrix`, `test_apply_blocked_while_pending` (E-12); FE `ImportReviewTable.test.tsx` |
| FR-IMP-008 transactional idempotent apply | Confirm dialog | `POST /imports/{id}:apply` | `purchases`, `stock_movements`, `products` | BE `test_apply_idempotent`, `test_apply_partial_failure_rolls_back` |
| FR-IMP-009 report | `ImportReportScreen` | `GET /imports/{id}/report` | `import_rows.apply_result` | BE `test_import_report_counts` |

---

## 10. Reports

| Requirement | Frontend behaviour | Backend endpoint | Entities | Tests |
|---|---|---|---|---|
| FR-RPT-001 daily sales incl. credit | `DailyBillsScreen` with date filter | `GET /reports/daily-sales` | `invoices`, `payments` | BE `test_daily_sales_includes_credit` |
| FR-RPT-002 credit outstanding | `CreditBillsScreen` | `GET /reports/credit-outstanding` | `invoices`, allocations | BE `test_credit_outstanding_report` |
| FR-RPT-003 the two reconcile | Both read the same API data | both | `invoices` | BE `test_daily_and_credit_reconcile` ⭐ |
| FR-RPT-006 counter reconciliation | `CounterReconciliationScreen` | `GET /reports/counter-reconciliation` | `payments.counter_id` | BE `test_counter_cash_reconciliation` |
| FR-RPT-007 CSV export | Export button | `GET /reports/{name}/export` | — | BE `test_csv_injection_escaped` |
| FR-RPT-008 cancelled excluded from revenue | Shown separately | all reports | `invoices.status` | BE `test_cancelled_excluded_from_revenue` |

---

## 11. Tax

| Requirement | Frontend behaviour | Backend endpoint | Entities | Tests |
|---|---|---|---|---|
| FR-TAX-001 `estimate_only` default | No tax row rendered when `tax` is `null` | all sale/print endpoints | `settings.tax.mode` | BE `test_estimate_mode_no_tax_lines`; FE `test_no_tax_row_when_null` |
| FR-TAX-002 effective-dated rules | — | `GET /admin/tax/config` | `tax_config_versions`, `tax_rates` | BE `test_effective_dated_rate_resolution` |
| FR-TAX-003 production gate | Checklist UI listing what is missing | `POST /admin/tax/enable-gst` | `businesses.tax_identifier`, `products.tax_group_id` | BE `test_enable_gst_gate_checklist` (AC-11) |
| FR-TAX-004 never claim compliance | Title from payload only | — | `invoices.document_kind` | BE `test_no_response_contains_tax_invoice_in_estimate_mode` |

---

## 12. Invariants → tests

| Invariant | Enforcement | Test |
|---|---|---|
| NFR-DATA-001 balance = ledger | service + nightly job | `invariants/test_stock_ledger.py`, `property/test_stock_conservation.py` |
| NFR-DATA-002 invoice total composition | **DB CHECK** `ck_invoices__total` | `invariants/test_invoice_totals.py`, `property/test_money_conservation.py` |
| NFR-DATA-002b `subtotal` = Σ line totals | Service + reconciliation *(promoted from `DATA-MODEL.md §4` into `PRD.md §7.4` in Phase 2 closure — audit CF-9)* | `invariants/test_invoice_totals.py::subtotal_equals_line_sum` |
| NFR-DATA-003 amount_paid = allocations | same-transaction write | `invariants/test_payment_allocation.py` |
| NFR-DATA-004 allocations ≤ payment | service under lock | same |
| NFR-DATA-005 internal reference unique + sequential | in-transaction allocation + `uq_invoices__series_sequence`. **Gaplessness moot** — no customer-visible serial exists ([OD-3](OWNER-DECISIONS.md#od-3)); revives only under [OD-25](OWNER-DECISIONS.md#od-25) | `concurrency/test_numbering.py`, `property/test_numbering.py` |
| NFR-DATA-006 no mutation of financial rows | **DB privileges** | `invariants/test_immutability.py` |
| NFR-DATA-007 negative only via override | service gate | `property/test_no_negative_stock.py` |
| NFR-DATA-008 returns ≤ sold | **DB CHECK** | `invariants/test_return_bounds.py` |
| NFR-DATA-009 money is NUMERIC | migration review | `invariants/test_no_float_columns.py` |
| NFR-DATA-010 business_id present | NOT NULL | `invariants/test_tenant_columns.py` |

---

## 13. Acceptance criteria → owning tests

| AC | Covered by |
|---|---|
| AC-1 five-line cash sale (internal reference; **no customer-visible number**) | BE `test_finalize_cash_sale`, `test_no_invoice_number_in_print_payload`; FE E2E `happy-path-cash.spec.ts`, structural `test_no_invoice_number_rendered` |
| ~~AC-1a~~ | ❌ **WITHDRAWN** — no customer-visible serial exists to be gapless ([OD-3](OWNER-DECISIONS.md#od-3)) |
| AC-2 idempotent replay | BE `test_finalize_idempotent_replay` |
| AC-3 last-unit race | BE `concurrency/test_two_counters_last_unit` |
| AC-4 credit with part payment reconciles | BE `test_credit_with_part_payment`, `test_daily_and_credit_reconcile` |
| AC-5 20 drafts survive restart | FE E2E `twenty-drafts.spec.ts`, `restart-recovery.spec.ts` |
| AC-6 offline drafts, finalisation refused | FE E2E `offline-drafts.spec.ts`, `offline-finalize-refused.spec.ts`; BE `sync/` |
| AC-7 all invariants + randomised concurrency | BE `invariants/`, `property/`, `concurrency/` |
| AC-8 cross-business isolation matrix | BE `api/test_isolation.py` |
| AC-9 ambiguous import needs decisions, idempotent apply | BE `test_apply_blocked_while_pending`, `test_apply_idempotent` |
| AC-10 restore drill | Manual, `DEPLOYMENT.md §7.1`, recorded result |
| AC-11 no "Tax Invoice" in estimate mode | BE `test_no_response_contains_tax_invoice_in_estimate_mode`; FE structural |
| AC-12 partial return | BE `test_partial_return_full_effect` |

---

## 13A. Complete P0 coverage index

§2–§12 above map the critical end-to-end flows in detail. This section closes the matrix: **every
remaining P0 requirement**, with its owning module, verification method and named planned test. Added
in the Phase 1 closure audit, which found 92 P0 requirements covered by a specification but absent
from this matrix.

Verification-method key: **AT** automated test · **ST** structural/source test · **DB** database
constraint · **MAN** manual pre-go-live check (`TESTING-STRATEGY.md §9`).

### 13A.1 Backend P0 requirements not itemised above

| Requirement | Module / endpoint | Entities | Method | Named planned test |
|---|---|---|---|---|
| FR-AUTH-002 refresh rotation + reuse detection | `auth` · `POST /auth/refresh` | `refresh_tokens` | AT | `test_refresh_rotation_and_reuse_detection` |
| FR-AUTH-007 owner password reset, forced change | `auth` · `POST /admin/users/{id}:reset-password` | `users.must_change_password` | AT | `test_forced_password_change_blocks_api` |
| FR-ADMIN-001 business identity drives header | `admin` · `PATCH /admin/business` | `businesses` | AT | `test_business_identity_in_print_payload`, `test_empty_fields_print_nothing` |
| FR-ADMIN-002 branch-scoped inventory/series/reports | `admin` · all branch endpoints | `branches` | AT | `test_branch_scoping_matrix` |
| FR-ADMIN-003 counters own drafts, capacity ≥ 20 | `admin` · `PUT /admin/counters/{id}` | `counters.max_open_drafts` | AT + DB | `test_counter_min_draft_capacity` (`CHECK >= 20`) |
| FR-ADMIN-004 typed audited settings with defaults | `settings` · `PUT /admin/settings/{key}` | `settings` | AT | `test_settings_defaults_and_audit`, `test_no_hardcoded_business_rule` (ST) |
| FR-CAT-001 product core fields | `catalog` · `POST /products` | `products` | AT | `test_product_create_fields` |
| FR-CAT-002 aliases searchable + unique | `catalog` · `POST /products/{id}/aliases` | `product_aliases` | AT + DB | `test_alias_search_and_uniqueness` |
| FR-CAT-008 SKU unique per business | `catalog` | `uq_products__business_sku` | AT + DB | `test_duplicate_sku_rejected` |
| FR-DRAFT-001 client-generated draft id is the server id | `sales` · `PUT /drafts/{id}` | `draft_bills` | AT | `test_draft_upsert_roundtrip` (no id remapping) |
| FR-DRAFT-004 version/optimistic concurrency | `sales` · `PUT /drafts/{id}` | `draft_bills.version` | AT | `test_draft_version_conflict_returns_state` |
| FR-DRAFT-005 drafts never reserve stock | `sales` | `stock_movements` (absence) | AT | `test_draft_edit_creates_no_movements` |
| FR-DRAFT-006 line stores billed + catalog price + source | `sales` | `draft_bill_items` | AT | `test_draft_line_price_provenance` |
| FR-DRAFT-008 drafts editable offline, synced later | `sync` · `POST /sync/operations` | `draft_bills`, `sync_operations` | AT | `sync/test_offline_draft_upsert` |
| FR-INV-002 closed set of movement reasons | `inventory` | `stock_movements.reason` enum | AT + DB | `test_movement_reason_enum_closed` |
| FR-PUR-001 supplier fields | `purchases` · `PUT /suppliers/{id}` | `suppliers` | AT | `test_supplier_crud` |
| FR-PUR-002 purchase records supplier/doc/branch/lines | `purchases` · `POST /purchases` | `purchases`, `purchase_items` | AT | `test_purchase_draft_fields` |
| FR-PUR-004 confirmed receipts immutable | `purchases` | `purchases.status` | AT | `test_confirmed_purchase_immutable` |
| FR-CUST-001 customer fields | `customers` · `PUT /customers/{id}` | `customers`, phones, vehicles | AT | `test_customer_upsert_fields` |
| FR-CUST-006 vehicle normalised, entered form preserved | `customers` | `customer_vehicles` | AT | `test_vehicle_normalisation_preserves_input` |
| FR-PAY-002 explicit allocation rows, sum ≤ payment | `credit` · `POST /payments` | `payment_allocations` | AT | `test_allocation_sum_bounded`, `invariants/test_payment_allocation.py` |
| FR-PAY-006 allocation strategy stored on the payment | `credit` | `payments.allocation_strategy` | AT | `test_allocation_strategies` |
| FR-PAY-007 no over-allocation beyond outstanding | `credit` | `payment_allocations` | AT | `test_over_allocation_rejected` |
| FR-AUDIT-002 audit append-only | `audit` | `audit_logs` privileges | AT + DB | `invariants/test_immutability.py::audit_logs` |
| FR-AUDIT-003 before/after on sensitive changes | `audit` | `audit_logs.before_value/after_value` | AT | `test_sensitive_change_records_before_after` |
| FR-AUDIT-004 no secrets in audit rows | `audit` | `audit_logs` | AT | `test_audit_never_contains_secrets` |

### 13A.2 Frontend P0 requirements not itemised above

Frontend tests live in `../../bmfrontend/docs/TESTING-STRATEGY.md`.

| Requirement | Component / route | Backend dependency | Method | Named planned test |
|---|---|---|---|---|
| FR-NAV-001 billing is the landing route | `routes/` redirect | `GET /auth/me` permissions | AT | `test_cashier_lands_on_billing` |
| FR-NAV-002 ≤ 7 labelled sidebar items, permission-filtered | `AppSidebar` | permissions | AT | `AppSidebar.test.tsx` |
| FR-NAV-003 top bar shows branch/counter/user/sync | `AppTopBar` | `GET /sync/bootstrap` | AT | `AppTopBar.test.tsx` |
| FR-NAV-004 navigation never discards draft state | router + draft store | — | AT (E2E) | `nav-preserves-drafts.spec.ts` |
| FR-NAV-005 deep links for invoice/customer/product | router | respective GETs | AT | `deep-links.spec.ts` |
| FR-NAV-007 no modal where inline will do | all screens | — | ST | `test_modal_allowlist` |
| FR-BILL-001 search auto-focus on load and after each line | `ProductSearchBox` | — | AT | `test_search_refocus_after_add` |
| FR-BILL-002 fixed screen composition | `BillingCounter` | — | AT | `BillingCounter.layout.test.tsx` |
| FR-BILL-003 exact item-table columns | `BillItemsTable` | — | AT | `test_item_table_columns_exact` |
| FR-BILL-004 tab strip with state badges | `BillTabStrip` | — | AT | `BillTabStrip.test.tsx` |
| FR-BILL-005 ≥ 20 tabs, all reachable, keyboard nav | `BillTabStrip` | `counters.max_open_drafts` | AT (E2E) | `twenty-drafts.spec.ts` |
| FR-BILL-006 switching is local, never finalises | draft store | — | AT (E2E) | `draft-switch-preserves.spec.ts` |
| FR-BILL-007 at limit: explain, never auto-close | `BillTabStrip` | `409 DRAFT_LIMIT_REACHED` | AT | `test_draft_limit_closes_nothing` |
| FR-BILL-008 total recalculates immediately, labelled preview | `TotalsPanel` | — | AT | `test_totals_live_and_labelled` |
| FR-BILL-009 two primary actions with sub-labels | `BillActions` | — | AT | `test_action_labels_and_sublabels` (OD-14) |
| FR-BILL-010 header fields inline, not modal | `BillHeaderFields` | — | AT | `BillHeaderFields.test.tsx` |
| FR-BILL-011 empty-bill hint | `BillItemsTable` | — | AT | `test_empty_bill_hint` |
| **FR-BILL-013 `Save a Draft` = draft only** | `BillActions` (primary) | `PUT /drafts/{id}` only | AT + ST | `test_save_a_draft_no_finalize_call`, `test_save_a_draft_wording` |
| **FR-BILL-012 `Clear Draft`** clears the editable draft only; blocked while a finalisation is unconfirmed | `BillActions` (primary) | `DELETE /drafts/{id}` | AT | `test_clear_draft_confirms_line_count`, `test_clear_draft_blocked_while_unconfirmed` |
| FR-CAT-011 search keyboard model | `ProductSearchBox` | — | AT | `test_search_keyboard_nav` |
| FR-CAT-012 no-results + add-new affordance | `ProductSearchBox` | `catalog.write` | AT | `test_no_results_add_new` |
| FR-CAT-013 search failure preserves the query | `ProductSearchBox` | 5xx | AT | `test_search_error_preserves_query` (AC-FE-12) |
| FR-CAT-014 select adds line, default qty 1, refocus | `ProductSearchBox` | — | AT | `test_select_adds_line_defaults` |
| FR-ITEM-001 in-place qty/price editing | `BillItemsTable` | — | AT | `test_inline_cell_editing` |
| FR-ITEM-002 decimal recalculation per keystroke | `lib/money.ts` | — | AT | `money.test.ts` (100% branch) |
| FR-ITEM-003 inline validation blocks submit | `BillItemsTable` | — | AT | `test_line_validation_blocks_submit` |
| FR-ITEM-004 price override marked; read-only without permission | `BillItemsTable` | `billing.price.override` | AT | `test_price_override_permission` |
| FR-ITEM-005 stock warning is non-blocking | `BillItemsTable` | — | AT | `test_stock_warning_non_blocking` |
| FR-ITEM-006 predictable cell tab order | `BillItemsTable` | — | AT | `test_cell_tab_order` |
| FR-ITEM-007 remove with 5 s undo | `BillItemsTable` | — | AT | `test_remove_line_undo` |
| FR-ITEM-009 inactive-product line flagged | `BillItemsTable` | `PRODUCT_INACTIVE` | AT | `test_inactive_product_row` |
| FR-SALE-020 validate then one finalise request | `finalize.ts` | `POST /sales:finalize` | AT | `test_finalize_single_request` |
| FR-SALE-021 busy state, no double submit | `BillActions` | — | AT | `test_submit_disabled_in_flight` |
| FR-SALE-022 print after confirmation; confirmation card | `finalize.ts`, `PrintRoot` | 201 response | AT (E2E) | `happy-path-cash.spec.ts` |
| FR-SALE-023 invoice survives print failure | `PrintRoot` | — | AT (E2E) | `print-failure.spec.ts` (AC-FE-7) |
| FR-SALE-024 reprint never finalises | `InvoiceActions` | `GET /print-payload` | AT + ST | `reprint.spec.ts`, `test_reprint_calls_no_finalize` |
| FR-SALE-025 every error code has a specific UI | `errors.ts` + panels | error registry | AT + ST | `test_all_error_codes_handled` (AC-FE-5) |
| FR-SALE-026 lost response → not-confirmed state | `pending_finalizations` | network failure | AT (E2E) | `lost-response-retry.spec.ts` (AC-FE-4) |
| FR-SALE-027 key reused on retry, new only on edit | `finalize.ts` | `Idempotency-Key` | AT | `test_idempotency_key_lifecycle` |
| FR-SALE-028 refused offline + counter slip offer | `BillActions` | — | AT (E2E) | `offline-finalize-refused.spec.ts` (AC-FE-3) |
| **FR-CREDIT-020 credit finalises immediately, no intermediate page** | `BillActions` | `POST /sales:finalize` (`credit`) | AT + ST | `test_credit_finalizes_in_one_request`, `test_no_credit_details_route` |
| FR-CREDIT-021 only customer + valid bill required | `BillActions` | — | AT | `test_credit_requires_only_customer` |
| FR-CREDIT-022 inline customer select/create, client UUID | `CustomerQuickCreate` (header field) | `PUT /customers/{id}` | AT | `test_inline_customer_create_uuid`, `test_create_requires_name_only` |
| FR-CREDIT-023 duplicates surfaced, never auto-merged | `CustomerQuickCreate` | `POST /customers/check-duplicates` | AT | `test_duplicate_candidates_shown` |
| FR-CREDIT-024 same finalisation endpoint as cash | `finalize.ts` | `POST /sales:finalize` | ST | `test_single_finalization_path` |
| FR-CREDIT-025 initial payment optional, not requested at save | `BillActions` | finalise `payment` optional | AT | `test_credit_without_payment_is_unpaid` |
| FR-CREDIT-026 wording distinguishes draft/credit/cash | `BillActions`, confirmation card | — | AT | `test_credit_confirmation_states_stock_updated` (OD-14) |
| FR-CREDIT-029 no print required at save | `BillActions` | — | AT | `test_credit_save_opens_no_print_dialog` |
| FR-CREDIT-030 credit reprint uses the same layout | `InvoicePrintLayout` | `GET /print-payload` | AT (snapshot) | `test_cash_and_credit_layout_identical` |
| FR-CREDIT-031 optional header values preserved | `finalize.ts` | invoice snapshot | AT | `test_optional_header_fields_persisted` |
| FR-CREDIT-027 appears in Credit **and** Daily Bills | both lists | `GET /invoices` | AT (E2E) | `credit-appears-in-both.spec.ts` |
| FR-CREDIT-028 customer mandatory for credit | `BillActions` (disabled state + inline hint) | `409 CREDIT_CUSTOMER_REQUIRED` | AT | `test_credit_requires_customer`, `test_credit_button_disabled_without_customer` |
| FR-RPT-020 Daily Bills filters and totals | `DailyBillsScreen` | `GET /invoices`, `/reports/daily-sales` | AT | `DailyBillsScreen.test.tsx` |
| FR-RPT-021 Credit Bills grouping, status, ageing | `CreditBillsScreen` | `GET /credit/outstanding` | AT | `CreditBillsScreen.test.tsx` |
| FR-RPT-022 both views from the same invoice API | both | `GET /invoices` | ST | `test_single_invoice_data_source` |
| FR-RPT-023 **no paid-status control anywhere** | all screens | derived `payment_status` | ST | `test_no_paid_checkbox` (AC-FE-10) |
| FR-RPT-024 payment recording with idempotency key | `RecordPaymentDialog` | `POST /payments` | AT | `test_payment_key_reuse` |
| FR-RPT-025 View/Reprint always; Cancel when permitted | `InvoiceActions` | `billing.sale.cancel` | AT | `test_invoice_row_actions_permissions` |
| FR-OFFLINE-014 unambiguous sync indicator states | `SyncStatusIndicator` | — | AT | `SyncStatusIndicator.test.tsx` |
| FR-OFFLINE-015 watermarked unnumbered counter slip | `CounterSlipLayout` | — | AT (print) | `counter-slip.spec.ts` |
| FR-PRINT-010 payload rendered verbatim | `InvoicePrintLayout` | `print_payload` | AT + ST | `InvoicePrintLayout.test.tsx`, `test_no_client_computed_print_total` |
| FR-PRINT-011 title from payload; no "Tax Invoice" literal | `InvoicePrintLayout` | `document_title` | ST | `test_no_hardcoded_tax_invoice_string` (AC-FE-10) |
| FR-PRINT-012 `tax === null` renders no tax row | `InvoicePrintLayout` | `totals.tax` | AT | `test_no_tax_row_when_null` |
| FR-PRINT-013 reproduces the Excel reference layout | `InvoicePrintLayout` | — | AT (snapshot) + **MAN** | `print-layout.spec.ts` + owner sign-off ([OD-5](OWNER-DECISIONS.md#od-5)) |
| FR-PRINT-014 print styling isolated from app styling | `print.css` | — | ST | `test_print_css_isolated` |
| FR-PRINT-017 minimum-exact-decimal amount display (OD-23) | `InvoicePrintLayout`, `lib/money.ts` | amount strings | AT + property | `test_print_amount_formatting`, `test_format_roundtrip_preserves_value` |
| FR-PRINT-018 empty rows print blank (OD-23) | `InvoicePrintLayout` | — | AT | `test_empty_rows_blank_not_zero` |
| FR-PRINT-019 invoice number printed; placement switchable (OD-22) | `InvoicePrintLayout` | `invoice_number` | AT | `test_invoice_number_printed`, `test_number_slot_toggle_safe` |
| FR-PRINT-020 payment block on credit bills only (OD-22) | `InvoicePrintLayout` | `payment.*` | AT | `test_payment_block_credit_only`, `test_payment_figures_match_payload` |
| FR-PRINT-021 `ESTIMATE` label for cash and credit (OD-24) | `InvoicePrintLayout` | `document_title` | AT + ST | `test_estimate_label_both_sale_types`, `test_no_substitute_label_literals` |
| FR-PRINT-015 duplicate marking + print event | `InvoicePrintLayout` | `POST /print-events` | AT | `test_duplicate_watermark` |
| FR-AUTH-010 access token in memory only | `authClient.ts` | — | ST | `test_no_token_in_storage` (AC-FE-10) |
| FR-AUTH-011 single shared refresh, retry once | `api/client.ts` | `POST /auth/refresh` | AT | `test_single_refresh_on_concurrent_401` |
| FR-AUTH-012 refresh failure keeps drafts | `authClient.ts` | — | AT | `test_login_prompt_preserves_drafts` |
| FR-AUTH-013 specific, actionable login errors | `LoginScreen` | auth error codes | AT | `LoginScreen.test.tsx` |
| FR-AUTH-015 stable device id per browser profile | `db/meta.ts` | `X-Device-Id` | AT | `test_device_id_stable_across_reload` |

### 13A.2a Credit workflow change — owner-confirmed 2026-10-29 (OD-21 / ADR-033)

New and changed requirements from the owner's credit-workflow change, and the **20 planned tests**
the change specifically requires. **None has been written or executed.**

| # | Planned test | Requirement | Layer | Name |
|---|---|---|---|---|
| 1 | Cash finalisation moves stock **exactly once** | FR-SALE-001 | BE concurrency | `test_cash_sale_single_stock_movement` |
| 2 | Credit finalisation moves stock **exactly once** | FR-CREDIT-008 | BE concurrency | `test_credit_sale_single_stock_movement` |
| 3 | Credit sale is stored under the correct customer | FR-CREDIT-011 | BE integration | `test_credit_invoice_linked_to_customer` |
| 4 | One customer holds multiple independently-tracked invoices | FR-RPT-028 | BE integration | `test_multiple_invoices_per_customer_independent` |
| 5 | Credit sale succeeds with **no** optional customer fields | FR-CUST-010 | BE API | `test_credit_sale_name_only_customer` |
| 6 | Missing customer identity is rejected safely, no partial effect | FR-CREDIT-009 | BE API | `test_credit_without_customer_rejected_atomically` |
| 7 | `Save a Draft` never finalises or moves stock | FR-BILL-013 | FE + BE | `test_save_a_draft_no_finalize_call`, `test_draft_edit_creates_no_movements` |
| 8 | A credit invoice prints later in the **same** format as cash | FR-CREDIT-030 | FE print | `test_cash_and_credit_layout_identical` |
| 9 | Partial payment reduces outstanding correctly | FR-CREDIT-002 | BE integration | `test_partial_payment_reduces_outstanding` |
| 10 | Full payment flips derived status to `paid` | FR-CREDIT-003 | BE integration | `test_full_payment_derives_paid` |
| 11 | Cannot mark paid while a balance remains | FR-CREDIT-003 | BE API + FE ST | `test_no_route_sets_payment_status`, `test_no_paid_checkbox` |
| 12 | Duplicate payment submission records **one** payment | FR-PAY-004 | BE API | `test_duplicate_payment_once` |
| 13 | **Payment collection never moves stock again** | FR-CREDIT-010 | BE integration | `test_payment_creates_no_stock_movement` |
| 14 | Customer-level totals equal the sum of invoice balances | FR-RPT-030 | BE invariant | `invariants/test_customer_outstanding_matches_invoices` |
| 15 | Duplicate finalisation cannot create a second invoice | FR-SALE-002 | BE concurrency | `test_finalize_idempotent_replay` |
| 16 | Lost response recovers via the original idempotency key | FR-SALE-026 | FE E2E | `lost-response-retry.spec.ts` |
| 17 | Failed finalisation leaves no partial stock or financial effect | FR-SALE-001 | BE integration | `test_finalize_rollback_leaves_nothing` |
| 18 | Unauthorised payment/invoice changes rejected **and audited** | NFR-SEC-004 | BE API | `test_payment_permission_denied_audited` |
| 19 | Cash, credit and draft keep distinct semantics | FR-CREDIT-020 | FE E2E | `three-actions-distinct.spec.ts` |
| 20 | Owner bill layout validated against the real reference + printer | FR-PRINT-013 | FE snapshot + **MAN** | `print-layout.spec.ts` + owner sign-off ([OD-5](OWNER-DECISIONS.md#od-5)) |

Supporting coverage for the same change:

| Requirement | Module / endpoint | Method | Named planned test |
|---|---|---|---|
| FR-CUST-010 minimum identity = `customer_id` + name | `customers` | AT | `test_minimum_customer_identity` |
| FR-CUST-011 `customer_id` immutable; rename safe | `customers` | AT | `test_rename_preserves_invoice_links` |
| FR-CUST-012 referenced customer never hard-deleted | `customers` | AT + DB | `test_customer_deactivate_not_delete` |
| FR-CREDIT-012 optional values persisted | `sales` | AT | `test_optional_header_fields_persisted` |
| FR-RPT-028 customer-wise grouping, no per-customer entity | `GET /credit/customers` | AT + ST | `test_credit_customers_aggregate`, `test_no_per_customer_table` |
| FR-RPT-029 grouping keyed on id, not name | `GET /credit/customers` | AT | `test_grouping_survives_rename` |
| FR-RPT-031 settled invoices stay visible | `GET /credit/customers?include_settled=` | AT | `test_settled_invoices_retained` |
| FR-RPT-032 per-invoice view/print/record-payment | `CreditBillsScreen` | AT | `CreditBillsScreen.test.tsx` |
| FR-RPT-033 per-invoice payment history | `GET /payments?invoice_id=` | AT | `test_invoice_payment_history` |
| Offline credit refusal (invariant 2a) | `BillActions` | AT (E2E) | `offline-credit-refused.spec.ts` |

### 13A.3 Coverage statement

With §2–§12 and §13A together, **every P0 requirement in both PRDs appears in this matrix with a
named planned test and a verification method.**

**Recounted 2026-10-29 after the credit-workflow change (OD-21 / ADR-033):**

| | Count |
|---|---|
| Backend P0 requirements | **101** |
| Frontend P0 requirements | **93** |
| **Total P0** | **194** |
| **P0 missing from this matrix** | **0** — verified ID-by-ID in Phase 2 closure |

> *Corrected in Phase 2 closure (audit finding CF-2).* This table previously reported **88** frontend
> and **189** total, contradicting `PHASE-1-EXIT-CRITERIA.md` criterion 12 and Addendum 4 (both
> 93 / 194) — which cite *this section* as their evidence. Phase 2 recounted from the authoritative
> requirement tables (`^| FR-… | P0 |` rows in both PRDs) and confirmed **101 + 93 = 194**, then
> checked each of the 194 IDs individually against this matrix: **every one appears.** The coverage
> claim was always true; only the tally was wrong.
| **P0 missing from this matrix** | **0** |

P1 and P2 requirements are intentionally not exhaustively mapped here; they are mapped when their
phase is planned.

> These are **planned** tests. **None has been written or executed** — Phase 1 produced
> documentation only.

---

## 14. Coverage gaps as of Phase 1

| Gap | Reason | Resolution |
|---|---|---|
| **Print layout — bottom section** | **Partially resolved 2026-10-29.** The owner supplied a screenshot which **was inspected**; the top section is now transcribed from evidence. But it ends at worksheet row 30, so the **grand total, signature area, footer, print area, page size and margins remain unseen**. The original `.xlsx` was **not** available ([OD-5](OWNER-DECISIONS.md#od-5)) | Owner supplies the workbook, a bottom-scrolled screenshot, or a photo of a printed bill; `[PROPOSED]` elements approved ([OD-22](OWNER-DECISIONS.md#od-22), [OD-23](OWNER-DECISIONS.md#od-23)); printed sample signed off |
| Tax behaviour | Unconfirmed (Q-TAX-1/2) | Schema and gate exist; tests assert `estimate_only` only |
| Branch transfers (FR-INV-010) | Out of MVP scope | No tests; E-13 behaviour (refusal + reasoned adjustments) is tested |
| Offline finalisation (FR-OFFLINE-013) | Deferred (ADR-011) | Structural test asserts it is **absent**, which is the requirement |
| PDF extraction accuracy | Best-effort by design (FR-IMP-010) | Mandatory human review is what is tested, not parser accuracy |
| Real-device print and scanner behaviour | Hardware-dependent | Manual checklist, `TESTING-STRATEGY.md §9` |

---

## 15. Related documents

`docs/PRD.md` · `docs/API-SPECIFICATION.md` · `docs/DATA-MODEL.md` · `docs/TESTING-STRATEGY.md` ·
`docs/DECISIONS.md` · `../../bmfrontend/docs/PRD.md` · `../../bmfrontend/docs/UI-UX-SPECIFICATION.md` ·
`../../bmfrontend/docs/TESTING-STRATEGY.md`

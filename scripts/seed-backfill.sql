-- Make backfill of ledger 4437357 possible on an otherwise empty wallet-backend DB.
--
-- startBackfilling (internal/services/ingest_backfill.go) requires:
--   * END_LEDGER <= ingest_store.latest_ingest_ledger, and
--   * a non-zero "oldest ledger", which GetOldestLedger reads from the transactions table
--     (not from ingest_store). With an empty table it is 0 and the requested range is
--     reported as "No gaps to backfill" - nothing would be ingested.
-- So we pretend ledger 4437358 (the next ledger) is already ingested: one placeholder row in
-- transactions plus both cursors. Backfill then sees the gap [4437357, 4437357].
-- The placeholder is identifiable by its hash (32 x 0xEE) and is excluded from all assertions.
-- Close time of 4437358 = 1788210377 (from the Galexie export of that ledger).
INSERT INTO transactions (to_id, hash, fee_charged, result_code, ledger_number, ledger_created_at)
VALUES ((4437358::bigint << 32), decode(repeat('ee', 32), 'hex'), 0, 'SENTINEL_NOT_A_REAL_TX', 4437358,
        to_timestamp(1788210377));

INSERT INTO ingest_store (key, value) VALUES
  ('latest_ingest_ledger', '4437358'),
  ('oldest_ingest_ledger', '4437358')
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;

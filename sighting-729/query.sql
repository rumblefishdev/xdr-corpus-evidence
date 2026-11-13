-- Evidence queries for wallet-backend #729 (tx d9f78749...9fed, ledger 4437357).
-- The sentinel row from seed-backfill.sql (ledger 4437358) is excluded everywhere.
\pset footer off
\echo '== transactions in ledger 4437357'
SELECT ledger_number, encode(hash, 'hex') AS hash, result_code
FROM transactions WHERE ledger_number = 4437357 ORDER BY to_id;

\echo '== the #729 transaction'
SELECT count(*) AS tx_729_rows FROM transactions
WHERE hash = decode('d9f78749cd01297cd5f22c7f2c798c9eec93d56f399e1c4cc2f8977ca52b9fed', 'hex');

\echo '== SIGNER state changes in ledger 4437357'
SELECT sc.to_id, sc.state_change_category, sc.state_change_reason,
       encode(sc.account_id, 'hex') AS account_id_hex,
       encode(sc.signer_account_id, 'hex') AS signer_account_id_hex,
       length(sc.signer_account_id) AS signer_len,
       sc.signer_weight_old, sc.signer_weight_new
FROM state_changes sc
WHERE sc.ledger_number = 4437357 AND sc.state_change_category = 'SIGNER'
ORDER BY sc.to_id, sc.operation_id, sc.state_change_id;

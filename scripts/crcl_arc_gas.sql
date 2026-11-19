-- DuneSQL. Replace the two UTC dates for each complete month.
-- Publish only after the provider data agrees with the receipt/coverage
-- checks in docs/data-supplement-runbook.md. All fees remain raw integers.
WITH bounds AS (
    SELECT DATE '2026-10-01' AS start_day, DATE '2026-11-01' AS end_day
), blocks AS (
    SELECT CAST(time AS DATE) AS day, MIN(number) AS first_block, MAX(number) AS last_block,
           COUNT(*) AS block_rows, COUNT(DISTINCT number) AS unique_blocks,
           SUM(CAST(gas_used AS DECIMAL(38, 0))) AS block_gas
    FROM arc.blocks CROSS JOIN bounds
    WHERE time >= CAST(start_day AS TIMESTAMP) AND time < CAST(end_day AS TIMESTAMP)
    GROUP BY 1
), txs AS (
    SELECT block_date AS day, COUNT(*) AS tx_rows,
           COUNT(DISTINCT hash) AS unique_txs,
           SUM(CASE WHEN success = false THEN 1 ELSE 0 END) AS failed_txs
    FROM arc.transactions CROSS JOIN bounds
    WHERE block_date >= start_day AND block_date < end_day
    GROUP BY 1
), fees AS (
    SELECT block_date AS day, COUNT(*) AS fee_rows, COUNT(DISTINCT tx_hash) AS unique_fee_txs,
           SUM(CASE WHEN tx_fee_raw IS NULL OR gas_used IS NULL
                      OR currency_symbol IS NULL OR UPPER(currency_symbol) <> 'USDC'
                    THEN 1 ELSE 0 END) AS invalid_fee_rows,
           SUM(CAST(gas_used AS DECIMAL(38, 0))) AS receipt_gas,
           SUM(CAST(tx_fee_raw AS DECIMAL(38, 0))) AS fee_raw
    FROM gas.fees CROSS JOIN bounds
    WHERE blockchain = 'arc' AND block_month = start_day
      AND block_date >= start_day AND block_date < end_day
    GROUP BY 1
)
SELECT b.day, first_block, last_block, block_rows, unique_blocks, block_gas,
       COALESCE(tx_rows, 0) AS tx_rows, COALESCE(unique_txs, 0) AS unique_txs,
       COALESCE(failed_txs, 0) AS failed_txs,
       COALESCE(fee_rows, 0) AS fee_rows, COALESCE(unique_fee_txs, 0) AS unique_fee_txs,
       COALESCE(invalid_fee_rows, 0) AS invalid_fee_rows, COALESCE(receipt_gas, 0) AS receipt_gas,
       CAST(COALESCE(fee_raw, 0) AS VARCHAR) AS fee_raw
FROM blocks b LEFT JOIN txs t ON t.day = b.day LEFT JOIN fees f ON f.day = b.day
ORDER BY b.day;

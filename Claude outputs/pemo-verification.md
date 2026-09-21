# Pemo import — expected values

Import `Pemo_Export.xlsx` at `/expense-analysis/pemo/import`, then check these.
Every figure is computed from the source file, so any mismatch is a load bug, not a rounding one.

## After the first import

| Check | Expected |
|---|---|
| Rows in `pemo_transactions` | **4,232** |
| Rows with `category_source = 'unmapped'` | **0** |
| Every `amount_aed` | positive |
| Every `billing_amount_raw` | negative, unchanged from the export |

### By transaction type (all statuses)

| txn_type | rows |
|---|---:|
| purchase | 3,107 |
| International Fee | 953 |
| refund | 84 |
| topUp | 75 |
| cashback | 12 |
| walletTransfer | 1 |

### By status (all types)

| status | rows |
|---|---:|
| completed | 3,421 |
| declined | 695 |
| reversed | 100 |
| pending | 16 |

### Spend — `txn_type = 'purchase' AND status = 'completed'`

| Metric | Expected |
|---|---|
| SUM(amount_aed) | **3,416,500.84** |
| COUNT | **2,473** |
| Average ticket | 1,381.52 |
| Median ticket | 123.43 |

### Category split (same filter)

| Category | Spend | Txns |
|---|---:|---:|
| Procurement | 1,205,205 | 242 |
| Marketplace Purchases | 835,095 | 918 |
| Marketing & Advertising | 778,715 | 337 |
| Health & Pharmacy | 158,833 | 25 |
| Software & Subscriptions | 156,847 | 143 |
| Government & Compliance | 128,485 | 46 |
| Utilities & Telecom | 71,527 | 50 |
| Travel & Accommodation | 22,208 | 20 |
| Meals & Entertainment | 20,930 | 338 |
| Transport & Fuel | 17,237 | 304 |
| Professional Services | 10,524 | 3 |
| Office & Facilities | 6,045 | 21 |
| Logistics & Shipping | 3,298 | 13 |
| Recruitment | 1,028 | 10 |
| Insurance | 522 | 3 |

### Other

| Check | Expected |
|---|---|
| Declined purchases | 558 rows, AED 1,598,563 |
| Decline rate | 18.4% |
| Cross-border spend (`actual_currency <> 'AED'`) | 497 txns, AED 1,667,823 |
| SUM(fee_total) on completed purchases | AED 64,107 → 3.84% |
| Completed purchases with no `receipt_url` | 422, AED 555,885 |
| Completed purchases with `gl_account` | 42 (1.7%) |
| `export_status = 'exported'` | 1,916 of 2,473 |
| Top-ups | 75 rows, AED 2,573,000 |

## The check that matters most

**Import the same file a second time.** Row count must still be **4,232** and total spend
still **3,416,500.84**.

If the upsert key is wrong everything downstream doubles, and every other check on this page
still passes on the first import — which is exactly why this one is last and why it cannot be
skipped.

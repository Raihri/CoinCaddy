# Cash reconstruction — hand-checked expected outputs

Uses the Northstar fixture transactions (see `northstar_trading_expected.md`), posted through the import API.
All amounts are BDT minor units. Position is computed **as of 2026-10-16**. A balance is fresh if verified within 2 days.

## Inputs entered

| Account | Anchors | Coverage declared | Restriction |
| --- | --- | --- | --- |
| ACC-BANK | 01 Oct 18,500,000; 15 Oct 16,224,975 | 02–15 Oct (backed by the bank import) | 2,000,000 from 01 Oct (security deposit) |
| ACC-WALLET | 01 Oct 4,250,050; 15 Oct 6,300,035 | 02–09 Oct only | — |
| ACC-CASH | 20 Sep 1,200,000 (cash count) | none | — |

T004 (bank −1,000,000, 06 Oct) and T005 (wallet +1,000,000, 06 Oct) are an own-account transfer.

## Case 1 — balanced period (ACC-BANK, 01→15 Oct)

18,500,000 + (6,500,000 − 3,275,025 − 1,000,000 − 4,500,000) = 18,500,000 − 2,275,025 = **16,224,975** = closing anchor.
Residual **0**, no coverage gaps → status **balanced**. Evidence through 15 Oct (age 1) → **fresh**.
Verified balance 16,224,975; usable = 16,224,975 − 2,000,000 = **14,224,975**.

## Case 2 — missing period (ACC-WALLET, 01→15 Oct)

4,250,050 + (1,849,975 + 1,000,000 − 799,990) = 4,250,050 + 2,049,985 = **6,300,035** = closing anchor, so residual **0**.
Coverage stops at 09 Oct, so **10–15 Oct is a gap** → status **balanced_incomplete** (a zero residual does not prove completeness).
Latest anchor 15 Oct, no coverage after it → evidence through 15 Oct, fresh, verified 6,300,035, usable 6,300,035.

## Case 3 — stale anchor (ACC-CASH)

Only anchor 20 Sep 1,200,000; no coverage → evidence through 20 Sep, age **26 days** → **stale**.
Verified balance 1,200,000 (as of 20 Sep). T006 (−350,000, 08 Oct) is posted after that date and not covered → **1 uncovered movement, −350,000**.
The cash box is **listed as stale and not added** to spendable cash.

## Case 4 — unmatched transfer

If only T004 is declared an own transfer: status **unmatched**, missing side **incoming**, amount **1,000,000**; the bank account is flagged **needs_review**.
Before any link exists, the system suggests exactly one pair: **T004 → T005, 1,000,000**.
After linking T004 with T005: status **matched**, no suggestion, no unmatched issue.

## Spendable BDT (with T004–T005 matched)

Included (fresh): bank 14,224,975 + wallet 6,300,035 = **20,525,010**. Restricted (included accounts): **2,000,000**.
Stale, not summed: cash box 1,200,000. Missing: none.

# Financial Health — hand-checked expected values (October 2026, BDT)

Source: the eight Northstar fixture transactions (`northstar_trading.json`), posted through the import API.

## Classification used

| Ref | Amount | Classified as |
| --- | ---: | --- |
| T001 | +6,500,000 | operating receipt · sales |
| T003 | +1,849,975 | operating receipt · sales |
| T002 | −3,275,025 | operating payment · inventory/suppliers |
| T008 | −799,990 | operating payment · inventory/suppliers |
| T007 | −4,500,000 | operating payment · rent |
| T006 | −350,000 | operating payment · other expense |
| T004 / T005 | −1,000,000 / +1,000,000 | own-account transfer (linked in Cash reconstruction; never classified) |

## Expected summary

- Operating receipts: 6,500,000 + 1,849,975 = **8,349,975** (sales only).
- Operating payments: 3,275,025 + 799,990 + 4,500,000 + 350,000 = **8,925,015**
  - inventory/suppliers 4,075,015 · rent 4,500,000 · other expense 350,000.
- Net operating cash: 8,349,975 − 8,925,015 = **−575,040** (matches `northstar_trading_expected.md`).
- Operating margin: −575,040 / 8,349,975 = −0.068867… = −688.67 basis points → rounded half away from zero **−689 bp (−6.89%)**.
- Excluded own transfers: in **1,000,000**, out **1,000,000** (not revenue, not expense).
- Unclassified: **0** → summary complete.

## Financing proceeds are not revenue

Adding a +10,000,000 loan disbursement classified as *financing inflow* leaves operating receipts at **8,349,975** and reports financing in **10,000,000** separately.

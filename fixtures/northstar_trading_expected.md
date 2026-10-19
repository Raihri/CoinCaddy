# Northstar Trading fixture — hand-checked expected values

Synthetic data only. Units are BDT minor units (poisha); `BDT 1.00 = 100`.
Business timezone `Asia/Dhaka` (UTC+6, no DST). T007's UTC timestamp falls on 9 Oct, but its business date is 10 Oct.

## Per-account closing (opening + signed movements)

| Account | Opening | Movements | Closing |
| --- | ---: | --- | ---: |
| ACC-BANK | 18,500,000 | +6,500,000 −3,275,025 −1,000,000 −4,500,000 = −2,275,025 | **16,224,975** (BDT 162,249.75) |
| ACC-WALLET | 4,250,050 | +1,849,975 +1,000,000 −799,990 = +2,049,985 | **6,300,035** (BDT 63,000.35) |
| ACC-CASH | 1,200,000 | −350,000 | **850,000** (BDT 8,500.00) |

Opening total: 18,500,000 + 4,250,050 + 1,200,000 = **23,950,050**.
Closing total: 16,224,975 + 6,300,035 + 850,000 = **23,375,010**.
Check: 23,950,050 − 575,040 = 23,375,010 ✓

## Operating view (own transfers excluded)

- Operating receipts: T001 6,500,000 + T003 1,849,975 = **8,349,975**
- Operating payments: T002 3,275,025 + T006 350,000 + T007 4,500,000 + T008 799,990 = **8,925,015**
- Net operating cash: 8,349,975 − 8,925,015 = **−575,040**
- Own-transfer pair TG-1: −1,000,000 + 1,000,000 = **0** (must never count as revenue)

## End-of-day same-currency totals

| Business date | Movement | Total |
| --- | ---: | ---: |
| 01 Oct | — | 23,950,050 |
| 02 Oct | +6,500,000 | 30,450,050 |
| 03 Oct | −3,275,025 | 27,175,025 |
| 05 Oct | +1,849,975 | 29,025,000 |
| 06 Oct | 0 (transfer pair) | 29,025,000 |
| 08 Oct | −350,000 | 28,675,000 |
| 10 Oct | −4,500,000 | 24,175,000 |
| 12 Oct | −799,990 | **23,375,010** (lowest) |

Reserve: 8,000,000 (BDT 80,000.00); the lowest total stays above it. These totals are a fixture self-check only. The product must still keep usable cash separate by account.

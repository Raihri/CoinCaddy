# Cash Outlook — hand-checked expected values (`cash_outlook_case.json`, outlook-v1)

As of **2026-10-20** (day 1 = 21 Oct). Reserve **8,000,000** BDT. All amounts in BDT minor units.

## Opening cash

| Account | Usable | Freshness | Expected path | Cautious path |
| --- | ---: | --- | ---: | ---: |
| Bank | 10,000,000 | fresh | 10,000,000 | 10,000,000 |
| Wallet | 1,000,000 | stale | 1,000,000 | 0 (a stale positive balance is not relied on) |
| Cash | — | no balance | left out | left out |
| **Total** | | | **11,000,000** | **10,000,000** |

## Receipts: on time, late, missing

| Item | Due | Expected path | Cautious path |
| --- | --- | --- | --- |
| R1 INV-1 +5,000,000 (**on time**) | 25 Oct | 25 Oct | 25 Oct + 14 = **8 Nov** |
| R2 INV-2 +3,000,000 (**late**, overdue 10 days) | 10 Oct | 20 Oct + 7 = **27 Oct**, marked uncertain | **left out** |
| R3 INV-3 +2,000,000 (**missing**: disputed) | 22 Oct | left out | left out |

Payments (both paths): P2 RENT-1 −1,500,000 is overdue → **21 Oct** (no account given → Bank assumed). P1 BILL-1 −7,000,000 → **27 Oct**.
Restriction release: +500,000 on **15 Nov** (it ends 14 Nov), both paths.

## Expected path (BDT total)

21 Oct 11,000,000 − 1,500,000 = **9,500,000** · 25 Oct + 5,000,000 = **14,500,000** · 27 Oct + 3,000,000 − 7,000,000 = **10,500,000** · 15 Nov + 500,000 = **11,000,000**.
Lowest **9,500,000 on 21 Oct**; never below 8,000,000 → **no breach**.

## Cautious path (BDT total)

21 Oct 10,000,000 − 1,500,000 = **8,500,000** · 27 Oct − 7,000,000 = **1,500,000** · 8 Nov + 5,000,000 = **6,500,000** · 15 Nov + 500,000 = **7,000,000**.
Lowest **1,500,000 on 27 Oct**. **First reserve breach 27 Oct**: balance 1,500,000, gap 8,000,000 − 1,500,000 = **6,500,000**.

## Weekly (week k ends 20 Oct + 7k)

| Week | Ends | Expected close | Expected min | Cautious close | Cautious min |
| --- | --- | ---: | ---: | ---: | ---: |
| 1 | 27 Oct | 10,500,000 | 9,500,000 | 1,500,000 | 1,500,000 |
| 2 | 3 Nov | 10,500,000 | 10,500,000 | 1,500,000 | 1,500,000 |
| 3 | 10 Nov | 10,500,000 | 10,500,000 | 6,500,000 | 1,500,000 |
| 4 | 17 Nov | 11,000,000 | 10,500,000 | 7,000,000 | 6,500,000 |
| 13 | 19 Jan 2027 | 11,000,000 | 11,000,000 | 7,000,000 | 7,000,000 |

Per account, Bank expected: 10,000,000 → 8,500,000 (21 Oct) → 13,500,000 (25 Oct) → 9,500,000 (27 Oct) → 10,000,000 (15 Nov). Wallet stays 1,000,000 (expected) / 0 (cautious).

Evidence reported: Wallet stale, Cash missing balance, INV-3 disputed, RENT-1 assumed account.

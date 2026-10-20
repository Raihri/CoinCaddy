# SME Finance Copilot — proposed usability improvements

> **Status: Feature 1 is partially implemented in the app; Features 2–3 remain proposals.** The implemented work is listed below so this file stays aligned with the product.

## UX goal

Help a small-business owner answer four questions on every screen: **What am I seeing? Why does it matter? What should I do next? Is this information complete?** Use plain language, show the supporting record, and keep the owner in control of financial actions.

## Feature 1 — Guided money workspace

### Implemented in the current app update

- Grouped sidebar navigation into **Daily money**, **Plan & decide**, **Safety & approvals**, and **Ask**, with the current section opening automatically.
- Added a three-step Records guide that distinguishes importing, reviewing/posting, and verifying balance evidence.
- Clarified that statement upload is for bank or wallet records; invoices and bills are managed separately.
- Renamed the Records cash summary **Spendable now** and explained that restricted funds are subtracted and stale or missing accounts are excluded. Excluded accounts link to their details.
- Added plain-language explanations for verified, stale, missing, and needs-review account states, with an update-balance shortcut for owners and bookkeepers.
- Explained balance anchors and source coverage next to their controls, including that coverage does not create transactions or fix an incorrect balance.
- Clarified that unfinished Payment Review entries are saved for the current browser tab, while a dated report is only saved after running the review.

### Still proposed

The **Today / Next steps** panel, complete empty-state guidance across all modules, per-product eligibility improvement guidance, and broader usability testing remain backlog items.

### Problem to solve

The app has useful finance tools, but users can encounter terms such as *imported*, *posted*, *reconciled*, *coverage incomplete*, *stale*, and *unmatched* without a clear explanation of the difference or the next step. Important tasks are split across Records, Cash Outlook, Payment Review, Funding Matches, and Approvals.

### Proposed experience

| Improvement | What the user sees | Behavior |
| --- | --- | --- |
| **Today / Next steps panel** | A short, prioritized list such as “Review 2 duplicate rows,” “Verify the bank balance,” or “One supplier payment needs approval.” | Each item explains why it matters and links directly to the record or action. Hide completed items; do not create urgency for informational items. |
| **Plain-language status labels** | A short label plus a one-sentence explanation and next action. Example: **Imported — saved for review; not yet included in cash figures.** | Explain statuses beside the first use and in a small “What does this mean?” help panel. Keep the precise status available for audit and technical review. |
| **Guided import steps** | `Choose account → Review file → Resolve issues → Post records → Verify balance` with a clear current step. | Explain that importing saves a file, posting accepts selected rows into the ledger, and reconciliation checks ledger movements against balance evidence. Posting must never imply reconciliation. |
| **Decision-ready cash numbers** | Each headline amount has an “How this is calculated” link showing included accounts, excluded stale or restricted balances, date of evidence, and supporting records. | Keep calculations server-generated. Link source rows and explain missing evidence; do not let a chat response override the calculated figure. |
| **Useful empty states** | When a list is empty, explain what the empty state means and offer one relevant next step. | Examples: “No funding comparison yet” → **Compare options**; “No invoices yet” → **Add an invoice**. Do not show actions the user’s role cannot take. |
| **Eligibility explanations** | Each financing option says **Eligible**, **Not eligible**, or **Needs more information**, followed by the specific criteria and how to address them. | Separate hard eligibility rules from missing records and illustrative estimates. Never imply lender approval. |
| **Consistent action controls** | One clear primary action per panel; secondary actions are quieter; destructive, financial, and approval actions are visually distinct. | Use consistent labels, disabled-state explanations, loading feedback, and success/error messages across pages. Preserve the existing approval and sandbox requirements. |
| **Remembered in-progress work** | If a user leaves an unfinished review form, they can return to their draft or receive a clear warning before losing it. | Persist only the minimum needed, scoped to the active business and user. Never store passwords, bank credentials, or full uploaded documents in browser storage. Clearly show when a draft was last saved. |

### Acceptance checks

1. A first-time user can tell the difference between **imported**, **posted**, **reconciled**, and **coverage complete** without searching a manual.
2. Every account marked **Stale**, **Missing balance**, **Coverage incomplete**, or **Uncovered movements** explains what evidence is missing and links to the relevant account or import.
3. Every empty module tells the user what is absent and offers one appropriate next step, where their role allows it.
4. A financing option that is not eligible names the failed criterion; missing information is not presented as a failed criterion.
5. Leaving an unfinished form never silently submits an action. A saved draft is clearly distinguished from a submitted, posted, approved, or executed action.
6. Staff can see the same explanatory status information as allowed by their role, but cannot see or use owner-only controls.
7. At a narrow mobile width, the primary task and its next action remain visible without horizontal page scrolling; tables can be scrolled within their own container.
8. Keyboard users can reach every control, see focus, understand button names, and receive announced validation or status messages.

### Usability test script

Ask a new tester to complete these tasks without coaching:

1. Import a statement and explain whether the rows have been posted and whether the account is reconciled.
2. Find the reason an account is excluded from **Spendable now** and identify the next step to refresh it.
3. Find an overdue customer invoice and explain what must happen before a reminder is sent.
4. Find out why a funding option is not eligible and what record or condition would change that result.
5. Review a proposed supplier payment and explain which scenario breaches the reserve and who must approve an action.

Record completion, wrong turns, requests for help, and whether the tester can explain the outcome in their own words. Fix repeated points of confusion before adding more dashboard content.

---

## Feature 2 — Import-assisted source coverage

## Current state

The existing product can import CSV/XLSX records, show rejected rows and possible duplicates, post accepted transactions, and record source coverage for an account. A user currently enters the coverage dates and evidence on the account page. An import can be selected as evidence, but the app does not yet suggest a complete statement period, explain gaps before confirmation, or offer a single review-and-confirm step. A zero reconciliation residual already **does not** automatically mean coverage is complete.

## Feature

After a statement is uploaded and reviewed, show a **Suggested coverage** card for its account. The card should propose a date interval, summarize evidence and problems, and let an owner or bookkeeper confirm the interval in one action. They should not need to enter every date or inspect every transaction merely to find the statement's period.

### What the user sees

| Item | Behavior |
| --- | --- |
| Suggested dates | Prefer explicit statement start/end dates or statement metadata. If only transaction rows are available, show the first and last transaction dates as a **provisional row span**, not as proof that all dates are covered. |
| Review summary | Show account, currency, source file, proposed period, posted/selected/rejected row counts, possible duplicates, and existing coverage. Link problem counts to their rows. |
| Gaps | Show uncovered dates between balance anchors and any dates outside the proposed interval. A day with no transactions is **not automatically a missing statement day**; mark it unknown unless the statement period or other source proves coverage. |
| Confirmation | Enable **Confirm coverage** for a posted import only after required row decisions are complete and the proposed period is supported by the evidence. Let the user shorten or split the interval when necessary. |
| Result | Recalculate reconciliation and usable cash; show any remaining gaps and whether the period is Balanced, Zero residual/coverage incomplete, or Does not reconcile. |

### Important distinction

The CSV's earliest and latest transaction dates alone cannot prove that the statement covers every date between them. A statement could have no transactions on a day, or pages/rows could be missing. When a file lacks trustworthy period metadata, label the proposed range **Needs confirmation** and require the reviewer to attest that the uploaded statement is complete for those dates. Do not silently declare coverage from a zero residual or from row dates alone.

## Implementation outline

1. Add optional statement-period metadata (start date, end date, statement reference, source type) to the import review flow. Keep it separate from transaction dates. Never guess an authoritative period from the row span.
2. Compute a coverage preview after mapping and row review: proposed interval, its evidence level, rejected rows, unresolved duplicates, current coverage, and remaining gaps between anchors. Make the preview read-only until the import is posted.
3. Add **Confirm coverage** to the posted import screen and account page. Reuse the existing coverage API, binding the confirmed period to that posted import. If the user edits the dates, validate that they remain within the supported statement interval and include the import's posted transaction dates.
4. Recalculate the account position after confirmation. Show the before/after status and link to the reconciliation period. If evidence is insufficient, leave the period incomplete and explain the exact missing information.
5. Keep manual confirmation for cash counts or sources without a usable digital statement, but make it an explicit exception with a note and source reference.

## Security and controls

- Only owner and bookkeeper roles may confirm coverage; staff may view the preview.
- Enforce permissions, account ownership, date limits, posted-import status, and period bounds on the server. Do not trust the UI or AI output to approve coverage.
- Preserve the imported file, row decisions, reviewer identity, timestamp, proposed/confirmed dates, evidence type, and any later void reason in the audit trail. Corrections must void the old declaration and create a new one.
- Treat filenames, statement notes, and invoice text as untrusted data. They cannot instruct the app to change coverage or permissions.
- Rejected rows and unresolved duplicates must remain visible. Confirmation must not silently post or discard them.

## Acceptance checks

1. A complete posted statement with an explicit 18–19 October period offers one coverage suggestion for those dates. One confirmation removes the gap, provided the reconciliation residual is zero.
2. If only transaction dates 18 and 19 October are known, the UI labels 18–19 October provisional and requires a completeness attestation; it does not auto-confirm.
3. If the user confirms only 18 October, 19 October remains a visible gap.
4. Rejected rows or unresolved duplicate decisions are surfaced before confirmation; the app never treats a partly reviewed import as complete evidence.
5. A zero residual with unconfirmed coverage stays **Zero residual, coverage incomplete**. A nonzero residual stays **Does not reconcile** even after coverage is confirmed.
6. Staff confirmation and a foreign-account import are rejected by the server and recorded as denied actions.

## Suggested place in the build plan

Add this as a focused enhancement after the existing import and cash-reconstruction features. It improves their handoff without changing the reconciliation formula or making an unsupported completeness claim.

---

## Feature 3 — Make balance freshness actionable

The current two-day freshness rule protects **Spendable now** from relying on an old balance, but the owner needs a faster way to update the evidence and a clearer explanation of what is missing. These additions extend the import-assisted coverage feature above.

| Priority | Feature | User experience | Implementation | Security and trust rule |
| --- | --- | --- | --- | --- |
| 1 | **Refresh balance** | An account-level button guides the owner through uploading a recent statement or entering a balance anchor from a statement. The app previews the suggested anchor, coverage dates, rejected rows, duplicates, and the resulting status before confirmation. | Reuse import review, balance-anchor, and coverage services. Add a server-generated preview and a guided confirmation screen; create the anchor and coverage through the existing permission-checked APIs. | Never silently create an anchor or confirm coverage. Require the appropriate owner/bookkeeper role, retain source reference and audit events, and keep rejected or unresolved rows visible. |
| 1 | **Specific freshness explanation** | Replace a generic “Stale” message with “Verified through 17 Oct; 18–20 Oct need evidence,” plus a link to the exact account/import review step. | Return `evidence_through`, age, uncovered dates, and suggested next action from the cash-position service. Render them on the account and Records pages. | Generate dates and amounts on the server from trusted records. An AI explanation may paraphrase them but cannot calculate or change the status. |
| 1 | **Separate old evidence from uncovered movements** | Show independent labels: **Old balance** when the verified date is too old, and **Uncovered movements** when posted transactions fall after the verified date. Show both when applicable. | Replace the single status shortcut with explicit freshness and coverage flags; preserve the reconciliation result separately. | Neither flag may be cleared by changing a label. Clearing it requires a new valid anchor or confirmed source coverage. |
| 2 | **Confirmed versus last-known cash** | Show confirmed Spendable now separately from the last-known amount on stale accounts, each with its evidence date. The stale figure is visible but clearly marked uncertain. | Keep the confirmed aggregate restricted to fresh accounts. Add a separate informational stale-account list and a date-stamped last-known total per currency; do not combine currencies. | Never add stale, missing, restricted, or unresolved cash to confirmed Spendable now. Do not display a last-known figure as available funds. |
| 2 | **Payment-specific evidence prompt — innovation** | In Payment Review, answer “What must I verify to make this payment?” with the smallest unresolved item that could change the decision, such as “Confirm the 19 Oct bank balance.” Show the answer under confirmed cash and a clearly labelled hypothetical case if stale cash were still available. | Reuse the payment scenario engine. Rank missing evidence by whether plausible changes could cross the reserve threshold, then by expected decision impact. Link each item to its source record or refresh action. | Hypothetical cash never authorizes payment. Require owner approval and current evidence for any sandbox action; preserve assumptions, source IDs, and decision changes in the audit log. |
| 3 | **Optional read-only account connection** | Where a bank and market support it, the owner may connect an account to retrieve current balances and transactions instead of repeatedly uploading files. | Use a provider-specific sandbox adapter first, then supported production APIs. Record retrieval time, balance type, account, and transaction coverage; run normal reconciliation and review checks. | Require explicit consent, least-privilege read access, secure token storage, revocation, and account isolation. A sync failure must never make old data appear fresh. Availability depends on the bank and region. |

## Acceptance checks for these improvements

1. A balance verified through 17 October on 20 October is labelled old, excluded from confirmed Spendable now, and linked to a concrete refresh action.
2. A recent anchor plus a posted transaction after the verified date shows **Uncovered movements** as its own reason, even if the anchor itself is not old.
3. The refresh preview shows what will change; cancelling it makes no data changes. Confirming valid evidence creates auditable records and updates the cash position.
4. A stale account's last-known amount is visible with its date but never included in the confirmed spendable figure or treated as authorization for payment.
5. Payment Review names the missing record that matters most to the current decision. Its hypothetical result remains clearly marked and cannot bypass approval or evidence checks.
6. A failed, expired, or revoked account connection leaves the previous verification date unchanged and alerts the owner.

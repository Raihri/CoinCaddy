// Application destinations. A destination stays `available: false` until its feature prompt is
// implemented and verified; the shell then renders an "unavailable" placeholder for it.
export type Destination = {
  path: string;
  label: string;
  monogram: string;
  available: boolean;
  plannedIn: string;
};

export const DESTINATIONS: Destination[] = [
  { path: "/overview", label: "Overview", monogram: "OV", available: false, plannedIn: "Prompt 09 (alerts) and later" },
  { path: "/records", label: "Records", monogram: "RC", available: true, plannedIn: "Prompts 05-06 (reconciliation, obligations)" },
  { path: "/financial-health", label: "Financial Health", monogram: "FH", available: true, plannedIn: "Prompt 07" },
  { path: "/cash-outlook", label: "Cash Outlook", monogram: "CO", available: false, plannedIn: "Prompt 08" },
  { path: "/payment-review", label: "Payment Review", monogram: "PR", available: false, plannedIn: "Prompt 10" },
  { path: "/funding-matches", label: "Funding Matches", monogram: "FM", available: false, plannedIn: "Prompt 12" },
  { path: "/fraud-monitor", label: "Fraud Monitor", monogram: "FR", available: false, plannedIn: "Prompt 16" },
  { path: "/approvals-audit", label: "Approvals & Audit", monogram: "AA", available: true, plannedIn: "Prompt 15 (approvals)" },
];

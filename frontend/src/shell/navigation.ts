import type { IconName } from "./NavIcon";

// Application destinations. A destination stays `available: false` until its feature prompt is
// implemented and verified; the shell then renders an "unavailable" placeholder for it.
export type Destination = {
  path: string;
  label: string;
  icon: IconName;
  available: boolean;
  plannedIn: string;
};

export const DESTINATIONS: Destination[] = [
  { path: "/overview", label: "Overview", icon: "overview", available: true, plannedIn: "Prompt 09 (alerts) and later" },
  { path: "/records", label: "Records", icon: "records", available: true, plannedIn: "Prompts 05-06 (reconciliation, obligations)" },
  { path: "/financial-health", label: "Financial Health", icon: "health", available: true, plannedIn: "Prompt 07" },
  { path: "/receivables-guardian", label: "Receivables Guardian", icon: "guardian", available: true, plannedIn: "Prompt 18" },
  { path: "/cash-outlook", label: "Cash Outlook", icon: "outlook", available: true, plannedIn: "Prompt 08" },
  { path: "/payment-review", label: "Payment Review", icon: "review", available: true, plannedIn: "Prompt 10" },
  { path: "/funding-matches", label: "Funding Matches", icon: "funding", available: true, plannedIn: "Prompt 12" },
  { path: "/conditional-funding", label: "Conditional Funding", icon: "conditional", available: true, plannedIn: "Prompt 19" },
  { path: "/finance-chat", label: "Finance Chat", icon: "chat", available: true, plannedIn: "Prompt 21" },
  { path: "/fraud-monitor", label: "Fraud Monitor", icon: "fraud", available: true, plannedIn: "Prompt 16" },
  { path: "/approvals-audit", label: "Approvals & Audit", icon: "approvals", available: true, plannedIn: "Prompt 15" },
];

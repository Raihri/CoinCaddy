import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { AuditPage } from "./audit/AuditPage";
import { FinancialHealthPage } from "./health/FinancialHealthPage";
import { CashOutlookPage } from "./outlook/CashOutlookPage";
import { OverviewPage } from "./overview/OverviewPage";
import { PaymentReviewPage } from "./review/PaymentReviewPage";
import { FundingMatchesPage } from "./funding/FundingMatchesPage";
import { FraudMonitorPage } from "./fraud/FraudMonitorPage";
import { ReceivablesGuardianPage } from "./guardian/ReceivablesGuardianPage";
import { ConditionalFundingPage } from "./conditional/ConditionalFundingPage";
import { ImportDetailPage } from "./records/ImportDetailPage";
import { RecordsPage } from "./records/RecordsPage";
import { AccountPage } from "./cash/AccountPage";
import { TransfersPage } from "./cash/TransfersPage";
import { ObligationDetailPage } from "./obligations/ObligationDetailPage";
import { ObligationsPage } from "./obligations/ObligationsPage";
import { SessionProvider, useSession } from "./auth/session";
import { SignInPage } from "./auth/SignInPage";
import { UsersRolesPage } from "./auth/UsersRolesPage";
import { BusinessSettingsPage } from "./settings/BusinessSettingsPage";
import { CreateBusinessPage } from "./settings/CreateBusinessPage";
import { AppShell } from "./shell/AppShell";
import { DESTINATIONS } from "./shell/navigation";
import { NotFoundPage, SystemStatusPage, UnavailablePage } from "./shell/pages";
import "./styles.css";

function App() {
  const { me, loading } = useSession();
  if (loading) return null;
  if (!me) return <SignInPage />;
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<SystemStatusPage />} />
        <Route path="/users-roles" element={<UsersRolesPage />} />
        <Route path="/business-settings" element={<BusinessSettingsPage />} />
        <Route path="/businesses/new" element={<CreateBusinessPage />} />
        <Route path="/approvals-audit" element={<AuditPage />} />
        <Route path="/overview" element={<OverviewPage />} />
        <Route path="/payment-review" element={<PaymentReviewPage />} />
        <Route path="/funding-matches" element={<FundingMatchesPage />} />
        <Route path="/fraud-monitor" element={<FraudMonitorPage />} />
        <Route path="/receivables-guardian" element={<ReceivablesGuardianPage />} />
        <Route path="/conditional-funding" element={<ConditionalFundingPage />} />
        <Route path="/records" element={<RecordsPage />} />
        <Route path="/financial-health" element={<FinancialHealthPage />} />
        <Route path="/cash-outlook" element={<CashOutlookPage />} />
        <Route path="/records/imports/:batchId" element={<ImportDetailPage />} />
        <Route path="/records/accounts/:accountId" element={<AccountPage />} />
        <Route path="/records/transfers" element={<TransfersPage />} />
        <Route path="/records/obligations" element={<ObligationsPage />} />
        <Route path="/records/obligations/:obligationId" element={<ObligationDetailPage />} />
        {DESTINATIONS.filter((d) => !d.available).map((d) => (
          <Route key={d.path} path={d.path} element={<UnavailablePage destination={d} />} />
        ))}
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <SessionProvider>
        <App />
      </SessionProvider>
    </BrowserRouter>
  </StrictMode>,
);

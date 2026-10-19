import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { AuditPage } from "./audit/AuditPage";
import { ImportDetailPage } from "./records/ImportDetailPage";
import { RecordsPage } from "./records/RecordsPage";
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
        <Route path="/records" element={<RecordsPage />} />
        <Route path="/records/imports/:batchId" element={<ImportDetailPage />} />
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

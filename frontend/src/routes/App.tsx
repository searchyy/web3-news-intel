import { Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "../auth/AuthContext";
import { AdminLayout } from "../layouts/AdminLayout";
import { AuditLogPage } from "../pages/AuditLogPage";
import { DashboardPage } from "../pages/DashboardPage";
import { DeliveriesPage } from "../pages/DeliveriesPage";
import { EventsPage } from "../pages/EventsPage";
import { FeishuGroupsPage } from "../pages/FeishuGroupsPage";
import { LoginPage } from "../pages/LoginPage";
import { RulesPage } from "../pages/RulesPage";
import { SourcesPage } from "../pages/SourcesPage";
import { SystemPage } from "../pages/SystemPage";

function Protected({ children }: { children: React.ReactNode }) {
  const { user } = useAuth();
  return user ? <>{children}</> : <Navigate to="/login" replace />;
}

export function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route
          path="/"
          element={
            <Protected>
              <AdminLayout />
            </Protected>
          }
        >
          <Route index element={<DashboardPage />} />
          <Route path="events" element={<EventsPage />} />
          <Route path="sources" element={<SourcesPage />} />
          <Route path="feishu-groups" element={<FeishuGroupsPage />} />
          <Route path="rules" element={<RulesPage />} />
          <Route path="deliveries" element={<DeliveriesPage />} />
          <Route path="system" element={<SystemPage />} />
          <Route path="audit" element={<AuditLogPage />} />
        </Route>
      </Routes>
    </AuthProvider>
  );
}

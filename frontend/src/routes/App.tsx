import { lazy, Suspense, type ReactNode } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "../auth/AuthContext";
import { LoginPage } from "../pages/LoginPage";

const AdminLayout = lazy(() => import("../layouts/AdminLayout").then((module) => ({ default: module.AdminLayout })));
const DashboardPage = lazy(() => import("../pages/DashboardPage").then((module) => ({ default: module.DashboardPage })));
const EventsPage = lazy(() => import("../pages/EventsPage").then((module) => ({ default: module.EventsPage })));
const SourcesPage = lazy(() => import("../pages/SourcesPage").then((module) => ({ default: module.SourcesPage })));
const FeishuGroupsPage = lazy(() =>
  import("../pages/FeishuGroupsPage").then((module) => ({ default: module.FeishuGroupsPage }))
);
const FeishuSettingsPage = lazy(() =>
  import("../pages/FeishuSettingsPage").then((module) => ({ default: module.FeishuSettingsPage }))
);
const AiSettingsPage = lazy(() => import("../pages/AiSettingsPage").then((module) => ({ default: module.AiSettingsPage })));
const RulesPage = lazy(() => import("../pages/RulesPage").then((module) => ({ default: module.RulesPage })));
const DeliveriesPage = lazy(() =>
  import("../pages/DeliveriesPage").then((module) => ({ default: module.DeliveriesPage }))
);
const SystemPage = lazy(() => import("../pages/SystemPage").then((module) => ({ default: module.SystemPage })));
const AuditLogPage = lazy(() => import("../pages/AuditLogPage").then((module) => ({ default: module.AuditLogPage })));

function Protected({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) {
    return null;
  }
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
              <Suspense fallback={null}>
                <AdminLayout />
              </Suspense>
            </Protected>
          }
        >
          <Route
            index
            element={
              <Suspense fallback={null}>
                <DashboardPage />
              </Suspense>
            }
          />
          <Route
            path="events"
            element={
              <Suspense fallback={null}>
                <EventsPage />
              </Suspense>
            }
          />
          <Route
            path="sources"
            element={
              <Suspense fallback={null}>
                <SourcesPage />
              </Suspense>
            }
          />
          <Route
            path="feishu-groups"
            element={
              <Suspense fallback={null}>
                <FeishuGroupsPage />
              </Suspense>
            }
          />
          <Route
            path="settings/feishu"
            element={
              <Suspense fallback={null}>
                <FeishuSettingsPage />
              </Suspense>
            }
          />
          <Route
            path="settings/ai"
            element={
              <Suspense fallback={null}>
                <AiSettingsPage />
              </Suspense>
            }
          />
          <Route
            path="rules"
            element={
              <Suspense fallback={null}>
                <RulesPage />
              </Suspense>
            }
          />
          <Route
            path="deliveries"
            element={
              <Suspense fallback={null}>
                <DeliveriesPage />
              </Suspense>
            }
          />
          <Route
            path="system"
            element={
              <Suspense fallback={null}>
                <SystemPage />
              </Suspense>
            }
          />
          <Route
            path="audit"
            element={
              <Suspense fallback={null}>
                <AuditLogPage />
              </Suspense>
            }
          />
        </Route>
      </Routes>
    </AuthProvider>
  );
}

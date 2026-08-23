import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { isRetryableError } from "./api/client";

import App from "./App";
import SchemaGate from "./SchemaGate";
import ChangesPage from "./pages/ChangesPage";
import AskPage from "./pages/AskPage";
import BlueprintPage from "./pages/BlueprintPage";
import CapabilitiesPage from "./pages/CapabilitiesPage";
import ConfigCheckPage from "./pages/ConfigCheckPage";
import SyslogPage from "./pages/SyslogPage";
import GlossaryPage from "./pages/GlossaryPage";
import ConfigPlansPage from "./pages/ConfigPlansPage";
import ConfigTemplatesPage from "./pages/ConfigTemplatesPage";
import ConfigsPage from "./pages/ConfigsPage";
import DocumentsPage from "./pages/DocumentsPage";
import DashboardPage from "./pages/DashboardPage";
import DeviceDetailPage from "./pages/DeviceDetailPage";
import DeviceFormPage from "./pages/DeviceFormPage";
import DeviceListPage from "./pages/DeviceListPage";
import ExposuresPage from "./pages/ExposuresPage";
import ImportPage from "./pages/ImportPage";
import InsightsPage from "./pages/InsightsPage";
import MigrationPage from "./pages/MigrationPage";
import ScenarioDetailPage from "./pages/ScenarioDetailPage";
import ReferencePage from "./pages/ReferencePage";
import RemediationPage from "./pages/RemediationPage";
import ReplacementPage from "./pages/ReplacementPage";
import ScenariosPage from "./pages/ScenariosPage";
import SettingsPage from "./pages/SettingsPage";
import SpacePage from "./pages/SpacePage";
import ExportPage from "./pages/ExportPage";
import TopologyPage from "./pages/TopologyPage";
import VlansPage from "./pages/VlansPage";
import VulnerabilitiesPage from "./pages/VulnerabilitiesPage";
import SitesPage from "./pages/SitesPage";
import "./styles.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: (count, error) => isRetryableError(error) && count < 1,
    },
    mutations: {
      retry: false,
    },
  },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/migration" element={<MigrationPage />} />
          <Route element={<SchemaGate />}>
            {/* 出力は印刷プレビューのような窓。メニューを出さず、中身に幅を使う */}
            <Route path="/export" element={<ExportPage />} />
            <Route path="/" element={<App />}>
              <Route index element={<Navigate to="/dashboard" replace />} />
              <Route path="dashboard" element={<DashboardPage />} />
              <Route path="ask" element={<AskPage />} />
              <Route path="blueprint" element={<BlueprintPage />} />
              <Route path="capabilities" element={<CapabilitiesPage />} />
              <Route path="config-check" element={<ConfigCheckPage />} />
              <Route path="syslog" element={<SyslogPage />} />
              <Route path="glossary" element={<GlossaryPage />} />
              <Route path="devices" element={<DeviceListPage />} />
              <Route path="devices/new" element={<DeviceFormPage />} />
              <Route path="devices/:id" element={<DeviceDetailPage />} />
              <Route path="devices/:id/edit" element={<DeviceFormPage />} />
              <Route path="sites" element={<SitesPage />} />
              <Route path="import" element={<ImportPage />} />
              <Route path="topology" element={<TopologyPage />} />
              <Route path="space" element={<SpacePage />} />
              <Route path="vlans" element={<VlansPage />} />
              <Route path="configs" element={<ConfigsPage />} />
              <Route path="changes" element={<ChangesPage />} />
              <Route path="insights" element={<InsightsPage />} />
              <Route path="vulnerabilities" element={<VulnerabilitiesPage />} />
              <Route path="reference" element={<ReferencePage />} />
              <Route path="documents" element={<DocumentsPage />} />
              <Route
                path="config-templates"
                element={<ConfigTemplatesPage />}
              />
              <Route path="config-plans" element={<ConfigPlansPage />} />
              <Route path="replacement" element={<ReplacementPage />} />
              <Route path="remediation" element={<RemediationPage />} />
              <Route path="exposures" element={<ExposuresPage />} />
              <Route path="scenarios" element={<ScenariosPage />} />
              <Route path="scenarios/:id" element={<ScenarioDetailPage />} />
              <Route path="settings" element={<SettingsPage />} />
            </Route>
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);

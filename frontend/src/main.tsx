import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import App from "./App";
import DashboardPage from "./pages/DashboardPage";
import DeviceDetailPage from "./pages/DeviceDetailPage";
import DeviceFormPage from "./pages/DeviceFormPage";
import DeviceListPage from "./pages/DeviceListPage";
import ImportPage from "./pages/ImportPage";
import SitesPage from "./pages/SitesPage";
import "./styles.css";

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false, retry: 1 } },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<App />}>
            <Route index element={<Navigate to="/dashboard" replace />} />
            <Route path="dashboard" element={<DashboardPage />} />
            <Route path="devices" element={<DeviceListPage />} />
            <Route path="devices/new" element={<DeviceFormPage />} />
            <Route path="devices/:id" element={<DeviceDetailPage />} />
            <Route path="devices/:id/edit" element={<DeviceFormPage />} />
            <Route path="sites" element={<SitesPage />} />
            <Route path="import" element={<ImportPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);

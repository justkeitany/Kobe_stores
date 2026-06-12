import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "react-hot-toast";
import { isAuthenticated } from "./lib/auth";
import Layout from "./components/Layout";
import Login from "./pages/Login";
import ChangePassword from "./pages/ChangePassword";
import Dashboard from "./pages/Dashboard";
import UsersPage from "./pages/UsersPage";
import Streams from "./pages/Streams";
import Categories from "./pages/Categories";
import Bouquets from "./pages/Bouquets";
import EPG from "./pages/EPG";
import ServerPage from "./pages/ServerPage";
import SettingsPage from "./pages/SettingsPage";

const qc = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 30_000,
    },
  },
});

function RequireAuth({ children }: { children: React.ReactNode }) {
  if (!isAuthenticated()) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

export default function App() {
  return (
    <QueryClientProvider client={qc}>
      <Toaster
        position="top-right"
        toastOptions={{
          style: { background: "#1f2937", color: "#f9fafb", border: "1px solid #374151" },
        }}
      />
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/change-password" element={
            <RequireAuth><ChangePassword /></RequireAuth>
          } />
          <Route
            path="/"
            element={
              <RequireAuth>
                <Layout />
              </RequireAuth>
            }
          >
            <Route index element={<Dashboard />} />
            <Route path="users" element={<UsersPage />} />
            <Route path="streams" element={<Streams />} />
            <Route path="categories" element={<Categories />} />
            <Route path="bouquets" element={<Bouquets />} />
            <Route path="epg" element={<EPG />} />
            <Route path="server" element={<ServerPage />} />
            <Route path="settings" element={<SettingsPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}

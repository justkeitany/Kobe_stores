import { useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import {
  LayoutDashboard, Tv, FolderOpen, Package, Radio,
  Server, Settings, LogOut, Menu, Users,
} from "lucide-react";
import { logout } from "../lib/auth";
import { useServerStats } from "../hooks/useServerStats";
import { useTheme } from "../lib/theme";
import { MIcon } from "./MIcon";
import clsx from "clsx";

const nav = [
  { label: "Dashboard",  icon: LayoutDashboard, path: "/" },
  { label: "Users",      icon: Users,           path: "/users" },
  { label: "Streams",    icon: Tv,              path: "/streams" },
  { label: "Categories", icon: FolderOpen,      path: "/categories" },
  { label: "Bouquets",   icon: Package,         path: "/bouquets" },
  { label: "EPG",        icon: Radio,           path: "/epg" },
  { label: "Server",     icon: Server,          path: "/server" },
  { label: "Settings",   icon: Settings,        path: "/settings" },
];

export default function Layout() {
  const [collapsed, setCollapsed] = useState(false);
  const { stats, connected } = useServerStats();

  return (
    <div className="flex h-screen bg-surface overflow-hidden">

      {/* ── Sidebar ─────────────────────────────────────────── */}
      <aside className={clsx(
        "flex flex-col bg-surface-container-lowest border-r border-outline-variant shrink-0 transition-all duration-200",
        collapsed ? "w-14" : "w-60"
      )}>

        {/* Brand */}
        <div className={clsx(
          "flex items-center h-16 px-md gap-2.5 shrink-0",
          collapsed && "justify-center px-0"
        )}>
          <div className="w-7 h-7 bg-primary flex items-center justify-center shrink-0">
            <Tv size={14} className="text-on-primary" />
          </div>
          {!collapsed && (
            <div className="min-w-0">
              <h1 className="font-bold text-on-surface text-base tracking-tighter leading-none">IPTV Admin</h1>
              <p className="font-code-label text-[10px] text-on-surface-variant opacity-60 mt-1">V2.4.1-Stable</p>
            </div>
          )}
          <button
            onClick={() => setCollapsed(!collapsed)}
            className={clsx(
              "text-on-surface-variant hover:text-on-surface transition-colors",
              collapsed ? "hidden" : "ml-auto"
            )}
          >
            <Menu size={16} />
          </button>
        </div>

        {/* Collapsed toggle */}
        {collapsed && (
          <button
            onClick={() => setCollapsed(false)}
            className="mx-auto mb-1 text-on-surface-variant hover:text-on-surface"
          >
            <Menu size={16} />
          </button>
        )}

        {/* Nav items (lucide icons — intentionally kept) */}
        <nav className="flex-1 px-sm py-1 overflow-y-auto space-y-0.5">
          {nav.map(({ label, icon: Icon, path }) => (
            <NavLink
              key={path}
              to={path}
              end={path === "/"}
              title={collapsed ? label : undefined}
              className={({ isActive }) =>
                clsx(
                  "flex items-center gap-3 px-md py-sm text-body-sm font-medium transition-colors border-r-2",
                  collapsed && "justify-center px-0",
                  isActive
                    ? "bg-surface-variant text-on-surface font-bold border-primary"
                    : "text-on-surface-variant border-transparent hover:bg-surface-container hover:text-on-surface"
                )
              }
            >
              <Icon size={16} className="shrink-0" />
              {!collapsed && <span>{label}</span>}
            </NavLink>
          ))}
        </nav>

        {/* Footer: CPU pill + Logout */}
        <div className="px-sm pt-md pb-3 space-y-1 shrink-0">
          {!collapsed && (
            <div className="px-md mb-sm">
              <div className="bg-surface-container-high px-3 py-2 border border-outline-variant flex items-center justify-between">
                <span className="flex items-center gap-2">
                  <span className={clsx(
                    "w-2 h-2 rounded-full",
                    connected ? "bg-green-500 animate-pulse" : "bg-gray-400"
                  )} />
                  <span className="font-code-label text-[10px] text-on-surface-variant">
                    {connected ? `CPU ${stats?.cpu_percent ?? 0}%` : "OFFLINE"}
                  </span>
                </span>
              </div>
            </div>
          )}
          <button
            onClick={() => logout()}
            title={collapsed ? "Logout" : undefined}
            className={clsx(
              "w-full flex items-center gap-3 px-md py-sm text-body-sm font-medium",
              "text-on-surface-variant hover:text-error hover:bg-surface-container transition-colors",
              collapsed && "justify-center px-0"
            )}
          >
            <LogOut size={16} className="shrink-0" />
            {!collapsed && <span>Logout</span>}
          </button>
        </div>
      </aside>

      {/* ── Main column ─────────────────────────────────────── */}
      <div className="flex-1 flex flex-col overflow-hidden">
        <TopHeader />
        <main className="flex-1 overflow-y-auto bg-surface">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

/* ── Top header bar (Stitch chrome) ──────────────────────────── */
function TopHeader() {
  const { theme, setTheme } = useTheme();
  const [q, setQ] = useState("");

  return (
    <header className="h-16 shrink-0 border-b border-outline-variant bg-surface flex items-center justify-between px-lg gap-lg z-30">
      {/* Search */}
      <div className="relative w-full max-w-md">
        <MIcon name="search" size={18}
          className="absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-variant pointer-events-none" />
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          className="input pl-10 pr-4 text-body-sm"
          placeholder="Search..."
          type="text"
        />
      </div>

      {/* Right cluster */}
      <div className="flex items-center gap-md shrink-0">
        {/* Theme toggle (segmented) */}
        <div className="flex items-center bg-surface-container p-1 border border-outline-variant">
          <button
            onClick={() => setTheme("dark")}
            title="Dark mode"
            className={clsx(
              "p-1 px-2 transition-colors",
              theme === "dark" ? "bg-primary text-on-primary" : "text-on-surface-variant hover:text-on-surface"
            )}
          >
            <MIcon name="dark_mode" size={18} />
          </button>
          <button
            onClick={() => setTheme("light")}
            title="Light mode"
            className={clsx(
              "p-1 px-2 transition-colors",
              theme === "light" ? "bg-primary text-on-primary" : "text-on-surface-variant hover:text-on-surface"
            )}
          >
            <MIcon name="light_mode" size={18} />
          </button>
        </div>

        <button className="relative p-2 text-on-surface-variant hover:text-on-surface hover:bg-surface-container transition-colors" title="Notifications">
          <MIcon name="notifications" size={22} />
          <span className="absolute top-1.5 right-1.5 w-1.5 h-1.5 bg-primary rounded-full" />
        </button>

        <div className="w-px h-6 bg-outline-variant" />

        <div className="flex items-center gap-2">
          <div className="text-right hidden sm:block">
            <p className="font-bold text-body-sm leading-tight">Admin User</p>
            <p className="font-code-label text-[10px] text-on-surface-variant uppercase tracking-widest">Superadmin</p>
          </div>
          <div className="w-8 h-8 bg-surface-container-highest border border-outline-variant flex items-center justify-center">
            <MIcon name="account_circle" size={22} className="text-on-surface-variant" />
          </div>
        </div>
      </div>
    </header>
  );
}

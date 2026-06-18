import { useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import {
  LayoutDashboard, Tv, FolderOpen, Package, Radio,
  Server, Settings, LogOut, Menu, Users,
} from "lucide-react";
import { logout } from "../lib/auth";
import { useServerStats } from "../hooks/useServerStats";
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
    <div className="flex h-screen bg-gray-50 overflow-hidden">

      {/* ── Sidebar ─────────────────────────────────────────── */}
      <aside className={clsx(
        "flex flex-col bg-white border-r border-gray-200 shrink-0 transition-all duration-200",
        collapsed ? "w-14" : "w-56"
      )}>

        {/* Logo row */}
        <div className={clsx(
          "flex items-center border-b border-gray-200 h-14 px-3 gap-2.5",
          collapsed && "justify-center"
        )}>
          <div className="w-7 h-7 bg-gray-900 flex items-center justify-center shrink-0">
            <Tv size={13} className="text-white" />
          </div>
          {!collapsed && (
            <span className="font-semibold text-gray-900 text-sm tracking-tight">
              IPTV Panel
            </span>
          )}
          <button
            onClick={() => setCollapsed(!collapsed)}
            className={clsx(
              "text-gray-400 hover:text-gray-700 transition-colors",
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
            className="mx-auto mt-2 text-gray-400 hover:text-gray-700"
          >
            <Menu size={16} />
          </button>
        )}

        {/* Nav items */}
        <nav className="flex-1 py-2 overflow-y-auto">
          {nav.map(({ label, icon: Icon, path }) => (
            <NavLink
              key={path}
              to={path}
              end={path === "/"}
              title={collapsed ? label : undefined}
              className={({ isActive }) =>
                clsx(
                  "flex items-center gap-3 px-3 py-2.5 mx-2 my-0.5 text-sm font-medium transition-colors border-l-2",
                  isActive
                    ? "bg-gray-100 text-gray-900 border-gray-900"
                    : "text-gray-500 border-transparent hover:text-gray-900 hover:bg-gray-100"
                )
              }
            >
              <Icon size={16} className="shrink-0" />
              {!collapsed && <span>{label}</span>}
            </NavLink>
          ))}
        </nav>

        {/* Live status pill */}
        {!collapsed && (
          <div className="px-4 py-2 border-t border-gray-200">
            <div className={clsx(
              "inline-flex items-center gap-1.5 text-xs font-medium px-2.5 py-1 border font-mono",
              connected
                ? "bg-gray-100 text-gray-700 border-gray-200"
                : "bg-gray-100 text-gray-500 border-gray-200"
            )}>
              <span className={clsx(
                "w-1.5 h-1.5 rounded-full",
                connected ? "bg-green-500 animate-pulse" : "bg-gray-400"
              )} />
              {connected ? `Live · CPU ${stats?.cpu_percent ?? 0}%` : "Connecting..."}
            </div>
          </div>
        )}

        {/* Logout */}
        <button
          onClick={() => logout()}
          title={collapsed ? "Logout" : undefined}
          className={clsx(
            "flex items-center gap-3 px-3 py-2.5 mx-2 mb-3 mt-1 text-sm font-medium",
            "text-gray-500 hover:text-red-400 hover:bg-gray-100 transition-colors"
          )}
        >
          <LogOut size={16} className="shrink-0" />
          {!collapsed && <span>Logout</span>}
        </button>
      </aside>

      {/* ── Main content ────────────────────────────────────── */}
      <main className="flex-1 overflow-y-auto bg-gray-50">
        <Outlet />
      </main>
    </div>
  );
}

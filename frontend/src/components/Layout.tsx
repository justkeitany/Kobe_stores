import { useState, useRef, useEffect } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import {
  LayoutDashboard, Tv, FolderOpen, Package, Radio,
  Server, Settings, LogOut, Menu, Users,
  type LucideIcon,
} from "lucide-react";
import { logout, currentUsername } from "../lib/auth";
import { useServerStats } from "../hooks/useServerStats";
import { useTheme } from "../lib/theme";
import { MIcon } from "./MIcon";
import clsx from "clsx";

const PLUTO_LOGO =
  "https://upload.wikimedia.org/wikipedia/commons/thumb/b/b4/Pluto_TV_Logo.svg/1200px-Pluto_TV_Logo.svg.png";

type NavItem = { label: string; path: string; icon?: LucideIcon; img?: string };

const nav: NavItem[] = [
  { label: "Dashboard",  icon: LayoutDashboard, path: "/" },
  { label: "Users",      icon: Users,           path: "/users" },
  { label: "Streams",    icon: Tv,              path: "/streams" },
  { label: "Pluto TV",   img: PLUTO_LOGO,       path: "/pluto" },
  { label: "Categories", icon: FolderOpen,      path: "/categories" },
  { label: "Bouquets",   icon: Package,         path: "/bouquets" },
  { label: "EPG",        icon: Radio,           path: "/epg" },
  { label: "Server",     icon: Server,          path: "/server" },
];

export default function Layout() {
  const [collapsed, setCollapsed] = useState(false);

  const footerLink = (active: boolean) =>
    clsx(
      "w-full flex items-center gap-3 px-md py-sm text-body-sm font-medium transition-colors",
      collapsed && "justify-center px-0",
      active
        ? "bg-surface-variant text-on-surface"
        : "text-on-surface-variant hover:bg-surface-container hover:text-on-surface"
    );

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
          <div className="w-7 h-7 rounded-md bg-brand flex items-center justify-center shrink-0">
            <Tv size={14} className="text-[#ffffff]" />
          </div>
          {!collapsed && (
            <h1 className="font-bold text-on-surface text-base tracking-tighter leading-none">IPTV Admin</h1>
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
          {nav.map(({ label, icon: Icon, img, path }) => (
            <NavLink
              key={path}
              to={path}
              end={path === "/"}
              title={collapsed ? label : undefined}
              className={({ isActive }) =>
                clsx(
                  "flex items-center gap-3 px-md py-sm text-body-sm font-medium transition-colors border-r-2 rounded-l-md",
                  collapsed && "justify-center px-0",
                  isActive
                    ? "bg-surface-variant text-on-surface font-bold border-primary"
                    : "text-on-surface-variant border-transparent hover:bg-surface-container hover:text-on-surface"
                )
              }
            >
              {img ? (
                <img src={img} alt="" className="w-5 h-5 object-contain shrink-0" />
              ) : Icon ? (
                <Icon size={16} className="shrink-0" />
              ) : null}
              {!collapsed && <span>{label}</span>}
            </NavLink>
          ))}
        </nav>

        {/* Footer: Settings + Logout */}
        <div className="px-sm pt-md pb-3 space-y-0.5 shrink-0 border-t border-outline-variant/40">
          <NavLink to="/settings" title={collapsed ? "Settings" : undefined}
            className={({ isActive }) => footerLink(isActive)}>
            <Settings size={16} className="shrink-0" />
            {!collapsed && <span>Settings</span>}
          </NavLink>
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

/* ── Top header bar (functional) ─────────────────────────────── */
function TopHeader() {
  const { theme, setTheme } = useTheme();
  const navigate = useNavigate();
  const { stats, connected } = useServerStats();
  const [q, setQ] = useState("");
  const [menu, setMenu] = useState<null | "bell" | "account">(null);
  const username = currentUsername();

  const closeMenu = () => setMenu(null);

  // Live alerts: any stream currently in an error state.
  const alerts = (stats?.streams ?? []).filter((s) => s.status === "error");

  function submitSearch(e: React.FormEvent) {
    e.preventDefault();
    const term = q.trim();
    if (!term) return;
    navigate(`/streams?q=${encodeURIComponent(term)}`);
  }

  return (
    <header className="h-12 shrink-0 border-b border-outline-variant bg-surface flex items-center justify-between px-lg gap-lg z-30">
      {/* Search */}
      <form onSubmit={submitSearch} className="relative flex-1 max-w-3xl">
        <MIcon name="search" size={18}
          className="absolute left-4 top-1/2 -translate-y-1/2 text-on-surface-variant pointer-events-none" />
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          className="input h-8 w-full pl-11 pr-4 text-body-sm rounded-full bg-surface-container-low border-outline-variant focus:bg-surface-container focus:border-on-surface"
          placeholder="Search streams…  press Enter"
          type="text"
        />
      </form>

      {/* Right cluster */}
      <div className="flex items-center gap-md shrink-0">
        {/* Theme toggle (segmented) */}
        <div className="flex items-center bg-surface-container p-1 border border-outline-variant rounded-md">
          <button
            onClick={() => setTheme("dark")}
            title="Dark mode"
            className={clsx(
              "p-1 px-2 rounded transition-colors",
              theme === "dark" ? "bg-primary text-on-primary" : "text-on-surface-variant hover:text-on-surface"
            )}
          >
            <MIcon name="dark_mode" size={18} />
          </button>
          <button
            onClick={() => setTheme("light")}
            title="Light mode"
            className={clsx(
              "p-1 px-2 rounded transition-colors",
              theme === "light" ? "bg-primary text-on-primary" : "text-on-surface-variant hover:text-on-surface"
            )}
          >
            <MIcon name="light_mode" size={18} />
          </button>
        </div>

        {/* Notifications */}
        <div className="relative">
          <button
            onClick={() => setMenu(menu === "bell" ? null : "bell")}
            className="relative p-1.5 text-on-surface-variant hover:text-on-surface hover:bg-surface-container rounded-md transition-colors"
            title="Notifications"
          >
            <MIcon name="notifications" size={20} />
            {alerts.length > 0 && (
              <span className="absolute top-1.5 right-1.5 w-1.5 h-1.5 bg-error rounded-full" />
            )}
          </button>
          {menu === "bell" && (
            <Dropdown onClose={closeMenu}>
              <div className="px-3 py-2 border-b border-outline-variant flex items-center justify-between">
                <span className="font-bold text-body-sm">Notifications</span>
                <span className="font-code-label text-[10px] text-on-surface-variant uppercase">
                  {connected ? "Live" : "Offline"}
                </span>
              </div>
              {alerts.length === 0 ? (
                <p className="px-3 py-4 text-body-sm text-on-surface-variant">
                  {connected ? "All systems operational." : "Connecting to server…"}
                </p>
              ) : (
                <ul className="max-h-64 overflow-y-auto">
                  {alerts.map((s) => (
                    <li key={s.id}>
                      <button
                        onClick={() => { closeMenu(); navigate("/streams"); }}
                        className="w-full text-left px-3 py-2 hover:bg-surface-container-high transition-colors flex items-start gap-2"
                      >
                        <MIcon name="error" size={16} className="text-error mt-0.5" />
                        <span className="text-body-sm">
                          Stream <span className="font-bold">#{s.id}</span> is in an error state
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </Dropdown>
          )}
        </div>

        <div className="w-px h-6 bg-outline-variant" />

        {/* Account */}
        <div className="relative">
          <button
            onClick={() => setMenu(menu === "account" ? null : "account")}
            className="flex items-center gap-2 hover:opacity-80 transition-opacity"
          >
            <div className="text-right hidden sm:block leading-tight">
              <p className="font-bold text-body-sm leading-tight">{username}</p>
            </div>
            <div className="w-7 h-7 bg-surface-container-highest border border-outline-variant rounded-md flex items-center justify-center">
              <MIcon name="account_circle" size={20} className="text-on-surface-variant" />
            </div>
          </button>
          {menu === "account" && (
            <Dropdown onClose={closeMenu}>
              <NavLink to="/change-password" onClick={closeMenu}
                className="flex items-center gap-2 px-3 py-2.5 text-body-sm hover:bg-surface-container-high transition-colors">
                <MIcon name="password" size={18} className="text-on-surface-variant" /> Change Password
              </NavLink>
              <NavLink to="/settings" onClick={closeMenu}
                className="flex items-center gap-2 px-3 py-2.5 text-body-sm hover:bg-surface-container-high transition-colors">
                <MIcon name="settings" size={18} className="text-on-surface-variant" /> Settings
              </NavLink>
              <button onClick={() => { closeMenu(); logout(); }}
                className="w-full text-left flex items-center gap-2 px-3 py-2.5 text-body-sm text-error hover:bg-surface-container-high transition-colors border-t border-outline-variant">
                <MIcon name="logout" size={18} /> Logout
              </button>
            </Dropdown>
          )}
        </div>
      </div>
    </header>
  );
}

/* Small dropdown with click-away backdrop. */
function Dropdown({ children, onClose }: { children: React.ReactNode; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    function onKey(e: KeyboardEvent) { if (e.key === "Escape") onClose(); }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <>
      <div className="fixed inset-0 z-40" onClick={onClose} />
      <div ref={ref}
        className="absolute right-0 mt-2 w-56 bg-surface-container border border-outline-variant rounded-md shadow-xl z-50 overflow-hidden">
        {children}
      </div>
    </>
  );
}

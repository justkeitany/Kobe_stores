import { useState, useRef, useEffect } from "react";
import { NavLink, Outlet, useNavigate, useLocation } from "react-router-dom";
import {
  LayoutDashboard, Tv, FolderOpen, Package, Radio,
  Server, Settings, LogOut, Menu, X, Users, Clapperboard, ChevronDown, ListVideo, LayoutGrid,
  type LucideIcon,
} from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { logout, currentUsername } from "../lib/auth";
import { useServerStats } from "../hooks/useServerStats";
import { useTheme } from "../lib/theme";
import { MIcon } from "./MIcon";
import api from "../lib/api";
import clsx from "clsx";

interface AiAlert {
  id: number; stream_id: number | null; title: string; detail: string | null;
  auto_applied: boolean; created_at: string;
}
function timeAgo(iso: string): string {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

const LOGOS = {
  pluto: "https://logo.keitanyfrank.store/Pluto-TV-Logo.png",
  roku: "https://logo.keitanyfrank.store/Roku-Logo.png",
  samsung: "https://logo.keitanyfrank.store/Samsung_TV_Plus_logo.png",
  plex: "https://logo.keitanyfrank.store/plex-logo.png",
  tubi: "https://logo.keitanyfrank.store/Tubi-Logo.png",
};

type NavLeaf = { label: string; path: string; icon?: LucideIcon; img?: string };
type NavGroup = { label: string; icon: LucideIcon; children: NavLeaf[] };
type NavEntry = NavLeaf | NavGroup;

const isGroup = (e: NavEntry): e is NavGroup => "children" in e;

// Tracks a CSS media query so JS render logic can match Tailwind breakpoints.
function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(
    () => typeof window !== "undefined" && window.matchMedia(query).matches
  );
  useEffect(() => {
    const mql = window.matchMedia(query);
    const onChange = () => setMatches(mql.matches);
    onChange();
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, [query]);
  return matches;
}

const nav: NavEntry[] = [
  { label: "Dashboard",  icon: LayoutDashboard, path: "/" },
  { label: "Users",      icon: Users,           path: "/users" },
  { label: "Streams",    icon: Tv,              path: "/streams" },
  {
    label: "Free Streams",
    icon: Clapperboard,
    children: [
      { label: "Pluto TV",         img: LOGOS.pluto,   path: "/pluto" },
      { label: "Plex",             img: LOGOS.plex,    path: "/freestreams/plex" },
      { label: "Samsung TV Plus",  img: LOGOS.samsung, path: "/freestreams/samsung" },
      { label: "Roku",             img: LOGOS.roku,    path: "/freestreams/roku" },
      { label: "Tubi",             img: LOGOS.tubi,    path: "/freestreams/tubi" },
    ],
  },
  { label: "Playlists",  icon: ListVideo,       path: "/playlists" },
  { label: "Channels",   icon: LayoutGrid,      path: "/channels" },
  { label: "Categories", icon: FolderOpen,      path: "/categories" },
  { label: "Bouquets",   icon: Package,         path: "/bouquets" },
  { label: "EPG",        icon: Radio,           path: "/epg" },
  { label: "Server",     icon: Server,          path: "/server" },
];

export default function Layout() {
  const [collapsed, setCollapsed] = useState(false);
  // Off-canvas drawer state for small screens (sidebar is hidden by default
  // on mobile and slides in over the content).
  const [mobileOpen, setMobileOpen] = useState(false);
  const { pathname } = useLocation();

  // Close the mobile drawer whenever the route changes (e.g. tapping a link).
  useEffect(() => { setMobileOpen(false); }, [pathname]);

  // The icon-rail collapse only applies on desktop; on mobile the drawer is
  // always shown full-width with labels.
  const isDesktop = useMediaQuery("(min-width: 1024px)");
  const railCollapsed = collapsed && isDesktop;

  // Track which collapsible nav groups are open. A group defaults to open when
  // the current route lives inside it.
  const [openGroups, setOpenGroups] = useState<Set<string>>(() => {
    const open = new Set<string>();
    for (const e of nav) {
      if (isGroup(e) && e.children.some((c) => pathname === c.path)) open.add(e.label);
    }
    return open;
  });

  const toggleGroup = (label: string) =>
    setOpenGroups((prev) => {
      const next = new Set(prev);
      next.has(label) ? next.delete(label) : next.add(label);
      return next;
    });

  const leafClass = (isActive: boolean, indented = false) =>
    clsx(
      "flex items-center gap-3 px-md py-sm text-body-sm font-medium transition-colors border-r-2 rounded-l-md",
      railCollapsed && "justify-center px-0",
      indented && !railCollapsed && "pl-9",
      isActive
        ? "bg-surface-variant text-on-surface font-bold border-primary"
        : "text-on-surface-variant border-transparent hover:bg-surface-container hover:text-on-surface"
    );

  const renderLeaf = ({ label, icon: Icon, img, path }: NavLeaf, indented = false) => (
    <NavLink
      key={path}
      to={path}
      end={path === "/"}
      title={railCollapsed ? label : undefined}
      className={({ isActive }) => leafClass(isActive, indented)}
    >
      {img ? (
        <img src={img} alt="" className="w-5 h-5 object-contain shrink-0" />
      ) : Icon ? (
        <Icon size={16} className="shrink-0" />
      ) : null}
      {!railCollapsed && <span>{label}</span>}
    </NavLink>
  );

  const footerLink = (active: boolean) =>
    clsx(
      "w-full flex items-center gap-3 px-md py-sm text-body-sm font-medium transition-colors",
      railCollapsed && "justify-center px-0",
      active
        ? "bg-surface-variant text-on-surface"
        : "text-on-surface-variant hover:bg-surface-container hover:text-on-surface"
    );

  return (
    <div className="flex h-screen bg-surface overflow-hidden">

      {/* Mobile backdrop — only shown while the drawer is open on small screens */}
      {mobileOpen && (
        <div
          className="fixed inset-0 bg-black/50 z-40 lg:hidden"
          onClick={() => setMobileOpen(false)}
          aria-hidden
        />
      )}

      {/* ── Sidebar ─────────────────────────────────────────── */}
      <aside className={clsx(
        "flex flex-col bg-surface-container-lowest border-r border-outline-variant shrink-0 transition-transform duration-200",
        // Mobile: fixed off-canvas drawer (always full width, never collapsed).
        "fixed inset-y-0 left-0 z-50 w-60",
        mobileOpen ? "translate-x-0" : "-translate-x-full",
        // Desktop: in-flow column that can collapse to an icon rail.
        "lg:static lg:translate-x-0 lg:z-auto lg:transition-all",
        collapsed ? "lg:w-14" : "lg:w-60"
      )}>

        {/* Brand */}
        <div className={clsx(
          "flex items-center h-16 px-md gap-2.5 shrink-0",
          railCollapsed && "justify-center px-0"
        )}>
          <div className="w-7 h-7 rounded-md bg-brand flex items-center justify-center shrink-0">
            <Tv size={14} className="text-[#ffffff]" />
          </div>
          {!railCollapsed && (
            <h1 className="font-bold text-on-surface text-base tracking-tighter leading-none">IPTV Admin</h1>
          )}
          {/* Desktop collapse toggle */}
          <button
            onClick={() => setCollapsed(!collapsed)}
            className={clsx(
              "text-on-surface-variant hover:text-on-surface transition-colors hidden lg:block",
              collapsed ? "lg:hidden" : "ml-auto"
            )}
          >
            <Menu size={16} />
          </button>
          {/* Mobile close button */}
          <button
            onClick={() => setMobileOpen(false)}
            className="text-on-surface-variant hover:text-on-surface transition-colors ml-auto lg:hidden"
            aria-label="Close menu"
          >
            <X size={18} />
          </button>
        </div>

        {/* Collapsed toggle (desktop icon-rail only) */}
        {railCollapsed && (
          <button
            onClick={() => setCollapsed(false)}
            className="mx-auto mb-1 text-on-surface-variant hover:text-on-surface"
          >
            <Menu size={16} />
          </button>
        )}

        {/* Nav items (lucide icons — intentionally kept) */}
        <nav className="flex-1 px-sm py-1 overflow-y-auto space-y-0.5">
          {nav.map((entry) => {
            if (!isGroup(entry)) return renderLeaf(entry);

            // When the sidebar is collapsed there's no room for a dropdown, so
            // surface the children directly as icon rows.
            if (railCollapsed) return entry.children.map((c) => renderLeaf(c));

            const { label, icon: Icon, children } = entry;
            const open = openGroups.has(label);
            const hasActiveChild = children.some((c) => pathname === c.path);
            return (
              <div key={label}>
                <button
                  onClick={() => toggleGroup(label)}
                  className={clsx(
                    "w-full flex items-center gap-3 px-md py-sm text-body-sm font-medium transition-colors border-r-2 rounded-l-md border-transparent",
                    hasActiveChild
                      ? "text-on-surface font-bold"
                      : "text-on-surface-variant hover:bg-surface-container hover:text-on-surface"
                  )}
                >
                  <Icon size={16} className="shrink-0" />
                  <span>{label}</span>
                  <ChevronDown
                    size={14}
                    className={clsx("ml-auto shrink-0 transition-transform", open && "rotate-180")}
                  />
                </button>
                {open && (
                  <div className="mt-0.5 space-y-0.5">
                    {children.map((c) => renderLeaf(c, true))}
                  </div>
                )}
              </div>
            );
          })}
        </nav>

        {/* Footer: Settings + Logout */}
        <div className="px-sm pt-md pb-3 space-y-0.5 shrink-0 border-t border-outline-variant/40">
          <NavLink to="/settings" title={railCollapsed ? "Settings" : undefined}
            className={({ isActive }) => footerLink(isActive)}>
            <Settings size={16} className="shrink-0" />
            {!railCollapsed && <span>Settings</span>}
          </NavLink>
          <button
            onClick={() => logout()}
            title={railCollapsed ? "Logout" : undefined}
            className={clsx(
              "w-full flex items-center gap-3 px-md py-sm text-body-sm font-medium",
              "text-on-surface-variant hover:text-error hover:bg-surface-container transition-colors",
              railCollapsed && "justify-center px-0"
            )}
          >
            <LogOut size={16} className="shrink-0" />
            {!railCollapsed && <span>Logout</span>}
          </button>
        </div>
      </aside>

      {/* ── Main column ─────────────────────────────────────── */}
      <div className="flex-1 flex flex-col overflow-hidden min-w-0">
        <TopHeader onMenuClick={() => setMobileOpen(true)} />
        <main className="flex-1 overflow-y-auto bg-surface">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

/* ── Top header bar (functional) ─────────────────────────────── */
function TopHeader({ onMenuClick }: { onMenuClick: () => void }) {
  const { theme, setTheme } = useTheme();
  const navigate = useNavigate();
  const { connected } = useServerStats();
  const [q, setQ] = useState("");
  const [menu, setMenu] = useState<null | "bell" | "account">(null);
  const username = currentUsername();

  const closeMenu = () => setMenu(null);

  // AI alerts feed (background monitor + manual "Issues?" results).
  const { data: aiAlerts = [] } = useQuery<AiAlert[]>({
    queryKey: ["ai-notifications"],
    queryFn: () => api.get("/ai/notifications?limit=25").then((r) => r.data),
    refetchInterval: 30_000,
  });
  const [seenId, setSeenId] = useState(() => Number(localStorage.getItem("ai_seen_id") || 0));
  const unseen = aiAlerts.filter((a) => a.id > seenId).length;
  function openBell() {
    setMenu(menu === "bell" ? null : "bell");
    if (aiAlerts.length) {
      const max = Math.max(...aiAlerts.map((a) => a.id));
      setSeenId(max);
      localStorage.setItem("ai_seen_id", String(max));
    }
  }

  function submitSearch(e: React.FormEvent) {
    e.preventDefault();
    const term = q.trim();
    if (!term) return;
    navigate(`/streams?q=${encodeURIComponent(term)}`);
  }

  return (
    <header className="h-12 shrink-0 border-b border-outline-variant bg-surface flex items-center justify-between px-md sm:px-lg gap-sm sm:gap-lg z-30">
      {/* Hamburger — opens the off-canvas sidebar on small screens */}
      <button
        onClick={onMenuClick}
        className="lg:hidden shrink-0 p-1.5 -ml-1 text-on-surface-variant hover:text-on-surface hover:bg-surface-container rounded-md transition-colors"
        aria-label="Open menu"
      >
        <Menu size={20} />
      </button>

      {/* Search */}
      <form onSubmit={submitSearch} className="relative flex-1 max-w-3xl min-w-0">
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
            onClick={openBell}
            className="relative p-1.5 text-on-surface-variant hover:text-on-surface hover:bg-surface-container rounded-md transition-colors"
            title="Notifications"
          >
            <MIcon name="notifications" size={20} />
            {unseen > 0 && (
              <span className="absolute -top-0.5 -right-0.5 min-w-[15px] h-[15px] px-0.5 bg-error text-on-error text-[9px] font-bold rounded-full flex items-center justify-center">
                {unseen > 9 ? "9+" : unseen}
              </span>
            )}
          </button>
          {menu === "bell" && (
            <Dropdown onClose={closeMenu}>
              <div className="px-3 py-2 border-b border-outline-variant flex items-center justify-between">
                <span className="font-bold text-body-sm">Notifications</span>
                <span className="font-code-label text-[10px] text-on-surface-variant uppercase">
                  AI watchdog {connected ? "· live" : ""}
                </span>
              </div>
              {aiAlerts.length === 0 ? (
                <p className="px-3 py-4 text-body-sm text-on-surface-variant">
                  All channels healthy. The AI checks in the background every 30 min.
                </p>
              ) : (
                <ul className="max-h-80 overflow-y-auto">
                  {aiAlerts.map((a) => (
                    <li key={a.id}>
                      <button
                        onClick={() => { closeMenu(); navigate("/channels"); }}
                        className="w-full text-left px-3 py-2.5 hover:bg-surface-container-high transition-colors flex items-start gap-2 border-b border-outline-variant/40 last:border-0"
                      >
                        <MIcon
                          name={a.auto_applied ? "auto_fix_high" : "warning"}
                          size={16}
                          className={clsx("mt-0.5 shrink-0", a.auto_applied ? "text-[#5edc8a]" : "text-[#f5c86e]")}
                        />
                        <span className="min-w-0">
                          <span className="block text-body-sm leading-snug">{a.title}</span>
                          <span className="block text-[10px] text-on-surface-variant mt-0.5">{timeAgo(a.created_at)}</span>
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

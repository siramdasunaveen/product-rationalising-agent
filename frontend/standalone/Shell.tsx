import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";
import { useManifest, useMe, type NavItem } from "../src";

function initials(name: string) {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]!.toUpperCase()).join("");
}

const NOT_SPECIFIED = "Not specified in the user guide yet";

function NavEntry({ item, label, className }: { item: NavItem; label: string; className: string }) {
  if (item.status === "not_specified") {
    return <span className={`${className} ${className}--disabled`} title={NOT_SPECIFIED}>{label}</span>;
  }
  return (
    <NavLink to={item.path} end={item.path === "/"}
      className={({ isActive }) => `${className}${isActive ? ` ${className}--active` : ""}`}>
      {label}
    </NavLink>
  );
}

/** Dev shell: header, top nav and WORKSPACE sidebar (screens/01). AI Studio provides its own. */
export function Shell({ children, onLogout }: { children: ReactNode; onLogout: () => void }) {
  const { data: me } = useMe();
  const { data: manifest } = useManifest();
  const perms = new Set(me?.permissions ?? []);
  const nav = (manifest?.nav ?? []).filter((n) => perms.has(n.permission));
  const sidebar = nav.filter((n) => n.sidebarLabel);

  return (
    <div className="shell">
      <header className="shell__header">
        <div className="shell__top">
          <div className="brand">
            <span className="brand__logo">DEEP<br />KORCE</span>
            <span>
              <span className="brand__title">Product Rationalisation Agent</span>
              <span className="brand__sub">Telco · Agentstudio</span>
            </span>
          </div>
          <label className="shell__search">
            <input placeholder="Search in Product Rationalisation" aria-label="Search in Product Rationalisation" />
            <svg viewBox="0 0 20 20" width="14" height="14" aria-hidden="true">
              <circle cx="8.5" cy="8.5" r="5.5" fill="none" stroke="currentColor" strokeWidth="1.8" />
              <path d="M13 13l4 4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
            </svg>
          </label>
          <div className="shell__user">
            {me && (
              <span className="shell__who">
                {me.name}
                <small>{me.tenant_id}</small>
              </span>
            )}
            <span className="shell__avatar" title={me?.name}>{me ? initials(me.name) : "…"}</span>
            <button className="shell__logout" onClick={onLogout}>Sign out</button>
          </div>
        </div>
        <nav className="shell__nav" aria-label="Main">
          {nav.map((n) => <NavEntry key={n.key} item={n} label={n.label} className="shell__tab" />)}
        </nav>
      </header>

      <div className="shell__body">
        <aside className="shell__sidebar" aria-label="Workspace">
          <div className="shell__sidebar-title">WORKSPACE</div>
          {sidebar.map((n) => (
            <NavEntry key={n.key} item={n} label={n.sidebarLabel!} className="shell__side-link" />
          ))}
        </aside>
        <main className="shell__main">{children}</main>
      </div>
    </div>
  );
}

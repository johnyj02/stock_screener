import { NavLink, Link } from "react-router-dom";

interface SidebarProps {
  runId: string;
}

const navItems = [
  { label: "Overview", slug: "overview" },
  { label: "Risk", slug: "risk" },
  { label: "Allocator", slug: "allocator" },
  { label: "Attribution", slug: "attribution" },
  { label: "Exit Quality", slug: "exits" },
  { label: "Journal", slug: "journal" },
  { label: "Charts", slug: "charts" },
  { label: "Daily Activity", slug: "daily-activity" }
];

export default function Sidebar({ runId }: SidebarProps) {
  return (
    <aside className="app-sidebar">
      <div className="brand">
        <div className="brand-mark" />
        <div>
          <div className="brand-title">Trade Journal</div>
          <div className="brand-subtitle">Institutional Insight</div>
        </div>
      </div>

      <div className="sidebar-section">
        <Link to="/" className="sidebar-link">Run Library</Link>
        <Link to="/compare" className="sidebar-link">Compare Runs</Link>
      </div>

      <div className="sidebar-section">
        <div className="sidebar-section-title">Run Views</div>
        {navItems.map((item) => (
          <NavLink
            key={item.slug}
            to={`/runs/${runId}/${item.slug}`}
            className={({ isActive }) =>
              isActive ? "sidebar-link active" : "sidebar-link"
            }
          >
            {item.label}
          </NavLink>
        ))}
      </div>
    </aside>
  );
}

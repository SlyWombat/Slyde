import { HashRouter, NavLink, Navigate, Route, Routes, useNavigate, useParams } from "react-router-dom";
import { FrameView } from "./components/FrameView";
import { useFrames } from "./lib/frames";
import { Activity } from "./pages/Activity";
import { Curate } from "./pages/Curate";
import { FleetDashboard } from "./pages/FleetDashboard";
import { FrameDetail } from "./pages/FrameDetail";
import { FramesList } from "./pages/FramesList";
import { Settings } from "./pages/Settings";
import { ToastProvider } from "./ui";

const NAV = [
  { to: "/", label: "Picture Frames", icon: "▦", end: true },
  { to: "/curate", label: "Curate", icon: "✦", end: false },
  { to: "/activity", label: "Activity", icon: "↻", end: false },
  { to: "/settings", label: "Settings", icon: "⚙", end: false },
];

function Brand() {
  return (
    <div className="px-3 text-xl font-extrabold tracking-tight">
      Sly<span className="text-accent">de</span>
    </div>
  );
}

function railLink(active: boolean) {
  return `flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition ${
    active ? "bg-accent/15 text-accent" : "text-slate-300 hover:bg-edge hover:text-slate-100"
  }`;
}

/** Persistent shell: left rail on desktop, bottom tab bar on mobile (#32). */
function Shell({ children }: { children: React.ReactNode }) {
  const { data } = useFrames();
  const attention = (data ?? []).some((f) => f.deliveries.failed > 0);

  return (
    <div className="min-h-screen md:pl-56">
      {/* Desktop rail */}
      <nav
        aria-label="Primary"
        className="fixed inset-y-0 left-0 hidden w-56 flex-col gap-1 border-r border-edge bg-panel/60 p-3 md:flex"
      >
        <div className="mb-4 mt-1">
          <Brand />
        </div>
        {NAV.map((n) => (
          <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => railLink(isActive)}>
            <span aria-hidden className="w-4 text-center">{n.icon}</span>
            {n.label}
          </NavLink>
        ))}
        <div className="mt-auto flex items-center gap-2 px-3 py-2 text-xs text-slate-400">
          <span className={`h-2 w-2 rounded-full ${attention ? "bg-red-400" : "bg-emerald-400"}`} />
          {attention ? "Needs attention" : "All healthy"}
        </div>
      </nav>

      {/* Mobile bottom tabs */}
      <nav
        aria-label="Primary"
        className="fixed inset-x-0 bottom-0 z-10 flex border-t border-edge bg-panel/95 backdrop-blur md:hidden"
      >
        {NAV.map((n) => (
          <NavLink
            key={n.to}
            to={n.to}
            end={n.end}
            className={({ isActive }) =>
              `flex flex-1 flex-col items-center gap-0.5 py-2 text-center text-[11px] font-medium leading-tight ${
                isActive ? "text-accent" : "text-slate-400"
              }`
            }
          >
            <span aria-hidden className="text-base">{n.icon}</span>
            {n.label}
          </NavLink>
        ))}
      </nav>

      <main className="min-w-0 pb-16 md:pb-0">{children}</main>
    </div>
  );
}

/** Legacy single-frame management (connected frames) — reachable until the new detail view (#36). */
function LegacyFrame() {
  const { host = "" } = useParams();
  const navigate = useNavigate();
  return <FrameView host={decodeURIComponent(host)} onBack={() => navigate("/")} />;
}

export default function App() {
  return (
    <ToastProvider>
      <HashRouter>
        <Shell>
        <Routes>
          <Route path="/" element={<FleetDashboard />} />
          <Route path="/frames" element={<FramesList />} />
          <Route path="/frames/:id" element={<FrameDetail />} />
          <Route path="/curate" element={<Curate />} />
          <Route path="/activity" element={<Activity />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/legacy/:host" element={<LegacyFrame />} />
          <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Shell>
      </HashRouter>
    </ToastProvider>
  );
}

import { useQuery } from "@tanstack/react-query";
import { api } from "./api/client";
import { FramePanel } from "./components/FramePanel";
import { SettingsPanel } from "./components/SettingsPanel";
import { AlbumBrowser } from "./components/AlbumBrowser";
import { OnFrame } from "./components/OnFrame";

export default function App() {
  const health = useQuery({ queryKey: ["health"], queryFn: api.health });

  return (
    <div className="mx-auto max-w-6xl px-4 py-6">
      <header className="mb-6 flex items-center justify-between">
        <h1 className="text-xl font-bold tracking-tight">
          Memento <span className="text-accent">Manager</span>
        </h1>
        {health.data && !health.data.immich_configured && (
          <span className="rounded-full bg-amber-500/15 px-3 py-1 text-xs text-amber-300">
            Immich not configured
          </span>
        )}
      </header>

      <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
        <div className="space-y-4">
          <AlbumBrowser />
        </div>
        <div className="space-y-4">
          <FramePanel />
          <SettingsPanel />
          <OnFrame />
        </div>
      </div>
    </div>
  );
}

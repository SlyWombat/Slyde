import { useState } from "react";
import { AddPhotos } from "./AddPhotos";
import { FrameAlbums } from "./FrameAlbums";
import { FramePanel } from "./FramePanel";
import { SettingsPanel } from "./SettingsPanel";

export function FrameView({ host, onBack }: { host: string; onBack: () => void }) {
  const [album, setAlbum] = useState<string | null>(null);

  return (
    <div className="mx-auto max-w-6xl px-4 py-6">
      <header className="mb-6 flex items-center gap-3">
        <button className="btn" onClick={onBack}>
          ‹ Frames
        </button>
        <h1 className="text-xl font-bold tracking-tight">
          Memento <span className="text-accent">Manager</span>
        </h1>
      </header>

      <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
        <div className="space-y-4">
          <FrameAlbums host={host} selected={album} onSelect={setAlbum} />
          <AddPhotos host={host} targetAlbum={album} />
        </div>
        <div className="space-y-4">
          <FramePanel host={host} />
          <SettingsPanel host={host} />
        </div>
      </div>
    </div>
  );
}

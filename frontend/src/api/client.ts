import type {
  Album,
  Asset,
  ConfigPatch,
  CurrentImage,
  FirmwareInfo,
  FrameAlbum,
  FrameDetailInfo,
  FrameInfo,
  FrameStatus,
  FrameSummary,
  FrameUpdate,
  Health,
  LibraryView,
  Subscription,
  SyncJobInfo,
  SyncResult,
} from "./types";

const BASE = "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: init?.body instanceof FormData ? {} : { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* keep statusText */
    }
    throw new Error(detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

const enc = encodeURIComponent;

export const api = {
  health: () => request<Health>("/health"),
  // Sync KPI as plain text ("OK …" / "FAIL …"), as served for Uptime Kuma. For the Activity view.
  syncHealth: async (): Promise<string> => (await fetch(`${BASE}/health/sync`)).text(),

  // -- frames --------------------------------------------------------------
  frames: () => request<FrameSummary[]>("/frames"), // LAN discovery (connected onboarding)
  // Manual active LAN scan (TCP-probe the control port). Discover-only — returns candidates and
  // adds nothing; the user adds one via addFrame. User-triggered only (#58).
  scanFrames: () => request<FrameSummary[]>("/frames/scan", { method: "POST" }),
  // Add a discovered/scanned connected frame to the registry by host/IP (explicit onboarding).
  addFrame: (host: string) =>
    request<FrameStatus>("/frames/add", { method: "POST", body: JSON.stringify({ host }) }),
  framesStatus: () => request<FrameStatus[]>("/frames/status"),
  frame: (host: string) => request<FrameInfo>(`/frames/${enc(host)}`),
  // Onboard a served/cloud frame by code so it appears in status before its first poll (#29/#35).
  registerFrame: (body: { frame_code: string; name?: string; backend?: string }) =>
    request<FrameStatus>("/frames/register", { method: "POST", body: JSON.stringify(body) }),
  // Deregister a frame: purge it from the registry + queue + library + cache. The device is untouched.
  deregisterFrame: (id: string) =>
    request<void>(`/frames/${enc(id)}`, { method: "DELETE" }),
  // Set a frame's registry display name (any backend) (#55).
  renameFrame: (id: string, name: string) =>
    request<FrameStatus>(`/frames/${enc(id)}`, { method: "PATCH", body: JSON.stringify({ name }) }),

  // -- per-frame library (transport-agnostic curation, #28/#37) -------------
  // The desired set + each photo's delivery state. Works for served/offline frames (no host calls).
  frameLibrary: (id: string) => request<LibraryView>(`/frames/${enc(id)}/library`),
  frameDetail: (id: string) => request<FrameDetailInfo>(`/frames/${enc(id)}/detail`),
  // How an Immich asset renders on this frame's panel (LCD JPEG vs e-ink palette PNG) (#30/#39).
  framePreviewUrl: (id: string, assetId: string) =>
    `${BASE}/frames/${enc(id)}/preview/${enc(assetId)}`,
  // Slyde's canonical, frame-independent preview for an asset — works for uploads (not in Immich)
  // and Immich assets alike, so library thumbnails render for every source.
  assetPreviewUrl: (assetId: string) => `${BASE}/assets/${enc(assetId)}/preview`,
  // Curate by asset id alone (dest_name derived server-side). Non-blocking: queues + reconciles.
  setLibrary: (id: string, items: { asset_id: string; dest_name?: string }[]) =>
    request<Record<string, number>>(`/frames/${enc(id)}/library`, {
      method: "PUT",
      body: JSON.stringify(items),
    }),
  updateConfig: (host: string, patch: ConfigPatch) =>
    request<FrameInfo>(`/frames/${enc(host)}/config`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),
  next: (host: string) => request<void>(`/frames/${enc(host)}/next`, { method: "POST" }),
  previous: (host: string) => request<void>(`/frames/${enc(host)}/previous`, { method: "POST" }),
  currentImage: (host: string) => request<CurrentImage>(`/frames/${enc(host)}/current`),

  albums: (host: string) => request<FrameAlbum[]>(`/frames/${enc(host)}/albums`),
  createAlbum: (host: string, name: string) =>
    request<FrameAlbum[]>(`/frames/${enc(host)}/albums`, {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
  deleteAlbum: (host: string, name: string) =>
    request<FrameAlbum[]>(`/frames/${enc(host)}/albums/${enc(name)}`, { method: "DELETE" }),
  removeFromAlbum: (host: string, name: string, filename: string) =>
    request<FrameAlbum[]>(`/frames/${enc(host)}/albums/${enc(name)}/images/${enc(filename)}`, {
      method: "DELETE",
    }),
  frameThumbUrl: (host: string, image: string) =>
    `${BASE}/frames/${enc(host)}/thumbnail/${enc(image)}`,
  deletePhoto: (host: string, filename: string) =>
    request<void>(`/frames/${enc(host)}/photos/${enc(filename)}`, { method: "DELETE" }),

  sync: (host: string, body: { album_id?: string; asset_ids?: string[]; target_album?: string }) =>
    request<SyncResult>(`/frames/${enc(host)}/sync`, { method: "POST", body: JSON.stringify(body) }),
  // Background sync: start a job, then poll syncJob() until status != "running".
  startSyncJob: (
    host: string,
    body: { album_id?: string; asset_ids?: string[]; target_album?: string },
  ) =>
    request<SyncJobInfo>(`/frames/${enc(host)}/sync/jobs`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  syncJob: (host: string, id: string) =>
    request<SyncJobInfo>(`/frames/${enc(host)}/sync/jobs/${enc(id)}`),
  upload: (host: string, files: File[], targetAlbum?: string) => {
    const form = new FormData();
    files.forEach((f) => form.append("files", f));
    if (targetAlbum) form.append("target_album", targetAlbum);
    return request<SyncResult>(`/frames/${enc(host)}/upload`, { method: "POST", body: form });
  },

  // -- subscriptions (keep an album in sync) -------------------------------
  subscriptions: (host: string) =>
    request<Subscription[]>(`/frames/${enc(host)}/subscriptions`),
  subscribe: (host: string, albumId: string, targetAlbum: string) =>
    request<SyncJobInfo>(`/frames/${enc(host)}/subscriptions`, {
      method: "POST",
      body: JSON.stringify({ album_id: albumId, target_album: targetAlbum }),
    }),
  unsubscribe: (host: string, albumId: string) =>
    request<void>(`/frames/${enc(host)}/subscriptions/${enc(albumId)}`, { method: "DELETE" }),

  // -- firmware / updates --------------------------------------------------
  firmware: () => request<FirmwareInfo>("/firmware"),
  checkFirmware: () => request<FirmwareInfo>("/firmware/check", { method: "POST" }),
  updateFrame: (host: string) =>
    request<FrameUpdate>(`/frames/${enc(host)}/update`, { method: "POST" }),

  // -- immich --------------------------------------------------------------
  immichAlbums: () => request<Album[]>("/immich/albums"),
  immichAssets: (albumId: string) => request<Asset[]>(`/immich/albums/${enc(albumId)}/assets`),
  immichThumbUrl: (assetId: string) => `${BASE}/immich/assets/${enc(assetId)}/thumbnail`,
};

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
  frames: () => request<FrameSummary[]>("/frames"),
  framesStatus: () => request<FrameStatus[]>("/frames/status"),
  frame: (host: string) => request<FrameInfo>(`/frames/${enc(host)}`),

  // -- per-frame library (transport-agnostic curation, #28/#37) -------------
  // The desired set + each photo's delivery state. Works for served/offline frames (no host calls).
  frameLibrary: (id: string) => request<LibraryView>(`/frames/${enc(id)}/library`),
  frameDetail: (id: string) => request<FrameDetailInfo>(`/frames/${enc(id)}/detail`),
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

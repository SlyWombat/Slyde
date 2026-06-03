import type {
  Album,
  Asset,
  ConfigPatch,
  FrameAlbum,
  FrameInfo,
  FrameSummary,
  Health,
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

  // -- frames --------------------------------------------------------------
  frames: () => request<FrameSummary[]>("/frames"),
  frame: (host: string) => request<FrameInfo>(`/frames/${enc(host)}`),
  updateConfig: (host: string, patch: ConfigPatch) =>
    request<FrameInfo>(`/frames/${enc(host)}/config`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),
  next: (host: string) => request<void>(`/frames/${enc(host)}/next`, { method: "POST" }),
  previous: (host: string) => request<void>(`/frames/${enc(host)}/previous`, { method: "POST" }),

  albums: (host: string) => request<FrameAlbum[]>(`/frames/${enc(host)}/albums`),
  createAlbum: (host: string, name: string) =>
    request<FrameAlbum[]>(`/frames/${enc(host)}/albums`, {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
  frameThumbUrl: (host: string, image: string) =>
    `${BASE}/frames/${enc(host)}/thumbnail/${enc(image)}`,
  deletePhoto: (host: string, filename: string) =>
    request<void>(`/frames/${enc(host)}/photos/${enc(filename)}`, { method: "DELETE" }),

  sync: (host: string, body: { album_id?: string; asset_ids?: string[]; target_album?: string }) =>
    request<SyncResult>(`/frames/${enc(host)}/sync`, { method: "POST", body: JSON.stringify(body) }),
  upload: (host: string, files: File[], targetAlbum?: string) => {
    const form = new FormData();
    files.forEach((f) => form.append("files", f));
    if (targetAlbum) form.append("target_album", targetAlbum);
    return request<SyncResult>(`/frames/${enc(host)}/upload`, { method: "POST", body: form });
  },

  // -- immich --------------------------------------------------------------
  immichAlbums: () => request<Album[]>("/immich/albums"),
  immichAssets: (albumId: string) => request<Asset[]>(`/immich/albums/${enc(albumId)}/assets`),
  immichThumbUrl: (assetId: string) => `${BASE}/immich/assets/${enc(assetId)}/thumbnail`,
};

import type {
  Album,
  Asset,
  ConfigPatch,
  FrameInfo,
  Health,
  SyncResult,
  SyncedPhoto,
} from "./types";

const BASE = "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
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

export const api = {
  health: () => request<Health>("/health"),
  frame: () => request<FrameInfo>("/frame"),
  updateConfig: (patch: ConfigPatch) =>
    request<FrameInfo>("/frame/config", { method: "PATCH", body: JSON.stringify(patch) }),
  next: () => request<void>("/frame/next", { method: "POST" }),
  previous: () => request<void>("/frame/previous", { method: "POST" }),

  albums: () => request<Album[]>("/immich/albums"),
  assets: (albumId: string) => request<Asset[]>(`/immich/albums/${albumId}/assets`),
  thumbUrl: (assetId: string) => `${BASE}/immich/assets/${assetId}/thumbnail`,

  sync: (body: { album_id?: string; asset_ids?: string[] }) =>
    request<SyncResult>("/sync", { method: "POST", body: JSON.stringify(body) }),
  photos: () => request<SyncedPhoto[]>("/photos"),
  deletePhoto: (assetId: string) =>
    request<void>(`/photos/${assetId}`, { method: "DELETE" }),
};

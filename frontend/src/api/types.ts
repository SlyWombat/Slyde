export interface FrameConfig {
  Name?: string;
  DisplayOn?: boolean;
  ShuffleOn?: boolean;
  NightModeOn?: boolean;
  PortraitMode?: boolean;
  DisplayTime?: number;
  SoftwareVersion?: number;
  ScreenSize?: number;
  Orientation?: string;
  [key: string]: unknown;
}

export interface FrameInfo {
  host: string;
  config: FrameConfig;
}

export interface FrameSummary {
  name: string;
  ip: string;
  mac: string;
  softver: number;
  hardver: number;
  size: number;
  orientation: string;
  guid: string;
}

export interface FrameAlbum {
  name: string;
  display_name: string;
  reserved: boolean;
  image_count: number;
  images: string[];
}

export interface Album {
  id: string;
  name: string;
  asset_count: number;
}

export interface Asset {
  id: string;
  file_name: string;
  type: string;
}

export interface SyncedPhoto {
  asset_id: string;
  dest_name: string;
  album_id: string | null;
  synced_at: string;
}

export interface SyncItem {
  asset_id: string;
  dest_name: string;
  status: "uploaded" | "skipped" | "failed";
  detail?: string | null;
}

export interface SyncResult {
  uploaded: number;
  skipped: number;
  failed: number;
  removed: number;
  items: SyncItem[];
}

export interface Subscription {
  immich_album_id: string;
  target_album: string;
  last_synced_at: string | null;
  last_result: string | null;
}

export interface Health {
  status: string;
  immich_configured: boolean;
  frame_reachable: boolean | null;
}

export interface ConfigPatch {
  DisplayOn?: boolean;
  ShuffleOn?: boolean;
  NightModeOn?: boolean;
  PortraitMode?: boolean;
  DisplayTime?: number;
  Name?: string;
}

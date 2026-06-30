export interface FrameConfig {
  Name?: string;
  DisplayOn?: boolean;
  ShuffleOn?: boolean;
  NightModeOn?: boolean;
  PortraitMode?: boolean;
  DisplayTime?: number;
  SoftwareVersion?: number; // Memento firmware version (float)
  AppVersion?: string; // soft-frame app/bundle version (semver), reported separately (#54)
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
  total: number;
  prepared: number;
  uploaded: number;
  skipped: number;
  failed: number;
  removed: number;
  items: SyncItem[];
}

export interface SyncJobInfo {
  id: string;
  host: string;
  label: string;
  status: "running" | "done" | "error";
  error: string | null;
  result: SyncResult;
}

export interface Subscription {
  immich_album_id: string;
  target_album: string;
  last_synced_at: string | null;
  last_result: string | null;
}

export interface Health {
  status: string;
  version: string;
  immich_configured: boolean;
}

export interface CurrentImage {
  image: string | null;
}

export interface FirmwareTrackInfo {
  track: string;
  version: string;
  md5: string;
}

export interface FirmwareInfo {
  repo: string;
  track: string;
  tracks: FirmwareTrackInfo[];
}

export interface FrameUpdate {
  sent: boolean;
  track: string;
  version: string;
  url: string;
}

export interface ConfigPatch {
  DisplayOn?: boolean;
  ShuffleOn?: boolean;
  NightModeOn?: boolean;
  PortraitMode?: boolean;
  DisplayTime?: number;
  Name?: string;
}

export interface DeliverySummary {
  pending: number;
  delivered: number;
  failed: number;
}

export interface FrameStatus {
  id: string;
  backend: string;
  interaction: string;
  transport?: string; // "lan" | "cloud" — only "lan" frames have a live current-image preview
  name: string;
  last_seen: string | null;
  deliveries: DeliverySummary;
}

/** One curated photo on a frame + its delivery state (#28 read-back). */
export interface LibraryPhoto {
  asset_id: string;
  dest_name: string;
  folder: string; // "" = the flat "All" view (#61)
  state: "delivered" | "pending" | "failed" | "unknown";
}

/** A frame's desired set (curation) joined with per-photo delivery state (#28). */
export interface LibraryView {
  items: LibraryPhoto[];
  deliveries: DeliverySummary;
}

export interface CapabilitiesInfo {
  interaction: string;
  transport: string;
  color_model: string;
  discovery: boolean;
  albums: boolean;
  thumbnails: boolean;
  upload: boolean;
  delete: boolean;
  ota: boolean;
}

/** Registry detail for a frame by id (any backend) + its backend capabilities (#28). */
export interface FrameDetailInfo {
  id: string;
  backend: string;
  interaction: string;
  name: string;
  address: string;
  frame_code: string;
  last_seen: string | null;
  capabilities: CapabilitiesInfo;
}

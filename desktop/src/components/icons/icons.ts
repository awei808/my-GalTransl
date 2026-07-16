/**
 * SVG icon path definitions.
 * All icons use a 24x24 viewBox.
 * - Stroke icons: fill="none", stroke="currentColor", stroke-width="1.5"
 * - Fill icons: fill="currentColor" (marked with fill: true)
 *
 * To add a new icon, add an entry to ICON_PATHS below.
 */

export interface IconDef {
  /** SVG path(s) — can be a single string or array for multi-path icons */
  d: string | string[];
  /** If true, use fill instead of stroke (for solid icons like play/square) */
  fill?: boolean;
}

export const ICON_PATHS: Record<string, IconDef> = {
  // ── Navigation ──
  home: {
    d: "M3 10.5 12 3l9 7.5M5 9.5V20a1 1 0 0 0 1 1h4v-6h4v6h4a1 1 0 0 0 1-1V9.5",
  },
  globe: {
    d: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18ZM3 12h18M12 3c2.5 2.5 3.8 5.7 3.8 9s-1.3 6.5-3.8 9c-2.5-2.5-3.8-5.7-3.8-9S9.5 5.5 12 3Z",
  },
  settings: {
    d: "M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7ZM19.4 13.5l1.8 1.4-2 3.4-2.2-.7a7.5 7.5 0 0 1-1.7 1l-.4 2.3h-3.8l-.4-2.3a7.5 7.5 0 0 1-1.7-1l-2.2.7-2-3.4 1.8-1.4a7.6 7.6 0 0 1 0-2l-1.8-1.4 2-3.4 2.2.7a7.5 7.5 0 0 1 1.7-1l.4-2.3h3.8l.4 2.3a7.5 7.5 0 0 1 1.7 1l2.2-.7 2 3.4-1.8 1.4a7.6 7.6 0 0 1 0 2Z",
  },

  // ── File / Folder ──
  folder: {
    d: "M3 7a2 2 0 0 1 2-2h4l2 2.5h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z",
  },
  'folder-open': {
    d: "M3 7a2 2 0 0 1 2-2h4l2 2.5h8a2 2 0 0 1 2 2V10M3 7v10a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7M3 7l1.5 5h17",
  },
  'file-text': {
    d: "M6 2h8l4 4v16H6V2Zm8 0v4h4M8 10h8M8 14h8M8 18h5",
  },

  // ── Data / Storage ──
  database: {
    d: "M12 3c-4.4 0-8 1.3-8 3v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6c0-1.7-3.6-3-8-3ZM4 6c0 1.7 3.6 3 8 3s8-1.3 8-3M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3",
  },
  book: {
    d: "M4 4v16h7a2 2 0 0 1 2-2 2 2 0 0 1 2 2h7V4h-7a2 2 0 0 0-2 2 2 2 0 0 0-2-2H4Zm9 2v14",
  },
  library: {
    d: "M5 3v18M9 3v18M13 3v18M17 3v18M21 3v18",
  },

  // ── People / User ──
  user: {
    d: "M12 7a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7ZM5 20a7 7 0 0 1 14 0",
  },

  // ── System / Backend ──
  cpu: {
    d: "M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3M6 6h12v12H6V6Zm3 3h6v6H9V9Z",
  },
  server: {
    d: "M4 7a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7Zm0 6a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-3ZM7 8h.01M7 15h.01",
  },

  // ── Status / Action ──
  loader: {
    d: "M12 3a9 9 0 0 1 9 9",
  },
  ban: {
    d: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18ZM5.6 5.6l12.8 12.8",
  },
  upload: {
    d: "M12 16V4m0 0L7 9m5-5 5 5M5 16v3a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-3",
  },
  download: {
    d: "M12 4v12m0 0 5-5m-5 5-5-5M5 16v3a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-3",
  },
  x: {
    d: "M6 6 18 18M18 6 6 18",
  },
  play: {
    d: "M7 4.5 19 12 7 19.5V4.5Z",
    fill: true,
  },
  'play-stroke': {
    d: "M7 4.5 19 12 7 19.5V4.5Z",
  },
  square: {
    d: "M6 6h12v12H6V6Z",
    fill: true,
  },
  check: {
    d: "M5 12l5 5 9-11",
  },
  'check-circle': {
    d: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18ZM8 12l3 3 5-6",
  },
  'alert-circle': {
    d: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18ZM12 8v5M12 16h.01",
  },

  // ── Config / Tools ──
  puzzle: {
    d: "M9 3a2 2 0 0 0-2 2v2H4v5h3a2 2 0 0 1 0 4H4v5h5v-3a2 2 0 0 1 4 0v3h5v-5h3a2 2 0 0 0 0-4h-3V7h-3V5a2 2 0 0 0-4 0v2H9V5a2 2 0 0 0-2-2Z",
  },
  search: {
    d: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14ZM16 16l4 4",
  },
  refresh: {
    d: "M3 12a9 9 0 0 1 15-6.7L21 8M21 3v5h-5M21 12a9 9 0 0 1-15 6.7L3 16M3 21v-5h5",
  },
  'refresh-ccw': {
    d: "M3 12a9 9 0 0 1 6.7-8.7L12 5M12 3v5M21 12a9 9 0 0 1-6.7 8.7L12 19M12 21v-5",
  },

  // ── Pipeline / Flow ──
  workflow: {
    d: "M4 4h6v6H4V4Zm0 10h6v6H4v-6Zm10-10h6v6h-6V4Zm3 9a3 3 0 0 0-3 3v4h6v-4a3 3 0 0 0-3-3ZM10 7h4M7 14v0a3 3 0 0 0 3 3h4",
  },
  inject: {
    d: "M18 2 22 6M15 5 19 9M3 21l9-9M12 12l3-3 4 4-3 3M9 15l-6 6",
  },

  // ── Misc ──
  'chevron-down': {
    d: "M5 9 12 16 19 9",
  },
  'chevron-right': {
    d: "M9 5 16 12 9 19",
  },
  'chevron-left': {
    d: "M15 5 8 12 15 19",
  },
  'chevron-up': {
    d: "M5 15 12 8 19 15",
  },
  plus: {
    d: "M12 5v14M5 12h14",
  },
  minus: {
    d: "M5 12h14",
  },
  trash: {
    d: "M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v6M14 11v6",
  },
  edit: {
    d: "M4 20h4L18 10l-4-4L4 16v4ZM14 6l4 4",
  },
  copy: {
    d: "M8 4h10a2 2 0 0 1 2 2v10M6 8h10a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V10a2 2 0 0 1 2-2Z",
  },
  'external-link': {
    d: "M14 4h6v6M20 4 10 14M18 13v5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h5",
  },
};

/** Get all icon names (for testing / validation) */
export function getIconNames(): string[] {
  return Object.keys(ICON_PATHS);
}

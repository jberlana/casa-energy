export type Source = "real" | "demo";
export interface Room {
  id: number;
  name: string;
  temperature: number | null;
  humidity: number | null;
  observed: number | null;
}
export interface Meta {
  source: Source;
  start: number;
  end: number;
  latest: number | null;
  updated: number | null;
  sampleMinutes: number;
  rooms: Room[];
}
export interface Interval {
  series: string;
  start: number;
  end: number;
  value: unknown;
}
export interface Data {
  source: Source;
  room: number;
  start: number;
  end: number;
  bucketMs: number;
  rawCount: number;
  samples: [number, number | null, number | null][];
  gaps: [number, number][];
  events: Interval[];
}
export type Range = { min: number; max: number };
export const HOUR = 3_600_000,
  DAY = 24 * HOUR;
export const fmt = (n: number | null | undefined, digits = 1) =>
  n == null
    ? "—"
    : n.toLocaleString("en-GB", {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      });
export const date = (
  t: number,
  options: Intl.DateTimeFormatOptions = {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  },
) =>
  new Intl.DateTimeFormat("en-GB", {
    timeZone: "Europe/Madrid",
    ...options,
  }).format(t);
export function target(value: unknown): number | null {
  const v = value as { power?: string; temperature?: { celsius?: number } };
  return v?.power === "ON" ? (v.temperature?.celsius ?? null) : null;
}
export const heating = (v: unknown) =>
  ["LOW", "MEDIUM", "HIGH", "HEATING"].includes(String(v));
export async function get<T>(url: string, signal?: AbortSignal): Promise<T> {
  const r = await fetch(url, {
    signal: AbortSignal.any([
      ...(signal ? [signal] : []),
      AbortSignal.timeout(15000),
    ]),
  });
  if (!r.ok)
    throw new Error("Could not read the history. Try again.");
  return r.json();
}

import { useDataRevision } from "../../lib/refresh";
import { useEffect, useState } from "react";
import { Sun, Moon, CloudSun, CloudMoon, Cloud, CloudFog, CloudDrizzle, CloudRain, CloudSnow, CloudLightning, CircleHelp, X } from "lucide-react";
import { DAY, HOUR, date, fmt, get, type Range, type Source } from "../../lib/types";

export interface WeatherHour {
  timestamp: number;
  temperature: number | null;
  precipitation: number | null;
  code: number | null;
  cloud: number | null;
  is_day: number | null;
  origin: string;
}
export interface Environment {
  sensor: string;
  outdoor: [number, number | null, number | null][];
  weather: WeatherHour[];
  days: { day: string; start: number; end: number; sunrise: number | null; sunset: number | null }[];
  firstOutdoor: number | null;
  hueStatus: { updated?: number; ok: boolean } | null;
  weatherStatus: { updated?: number; ok: boolean } | null;
}

export function daylightBands(data: Environment | null, range: Range) {
  const bands: { from: number; to: number; daylight: boolean }[] = [];
  if (!data || range.max - range.min > 7 * DAY) return bands;

  for (const day of data.days) {
    if (day.sunrise === null || day.sunset === null) continue;
    const intervals = [
      { from: day.start, to: day.sunrise, daylight: false },
      { from: day.sunrise, to: day.sunset, daylight: true },
      { from: day.sunset, to: day.end, daylight: false },
    ];
    for (const interval of intervals) {
      const from = Math.max(interval.from, range.min);
      const to = Math.min(interval.to, range.max);
      if (to <= from) continue;
      const previous = bands.at(-1);
      if (previous?.to === from && previous.daylight === interval.daylight) {
        previous.to = to;
      } else {
        bands.push({ from, to, daylight: interval.daylight });
      }
    }
  }
  return bands;
}

const cache = new Map<string, { value: Environment; at: number }>();
export function useEnvironment(source: Source, range: Range) {
  const revision = useDataRevision();
  const [data, setData] = useState<Environment | null>(null);
  useEffect(() => { cache.clear(); }, [revision]);
  const [failed, setFailed] = useState(false);
  const start = Math.floor(range.min / DAY) * DAY - DAY;
  const end = Math.ceil(range.max / DAY) * DAY + DAY;
  useEffect(() => {
    if (source === "demo") { setData(null); setFailed(false); return; }
    const controller = new AbortController();
    const url = `/api/environment?start=${start}&end=${end}`;
    let active = true;
    async function load() {
      try {
        const saved = cache.get(url);
        const value = saved && Date.now() - saved.at < 55_000 ? saved.value : await get<Environment>(url, controller.signal);
        if (!active) return;
        cache.set(url, { value, at: Date.now() });
        while (cache.size > 4) cache.delete(cache.keys().next().value!);
        setData(value); setFailed(false);
      } catch { if (active) setFailed(true); }
    }
    const debounce = setTimeout(load, 150);
    const timer = setInterval(() => { if (document.visibilityState === "visible") load(); }, 60_000);
    return () => { active = false; controller.abort(); clearTimeout(debounce); clearInterval(timer); };
  }, [source, start, end, revision]);
  return { data, failed };
}

export function condition(code: number | null, day: boolean) {
  if (code === null) return { Icon: CircleHelp, label: "Sin dato", tone: "cloud" };
  if (code === 0) return { Icon: day ? Sun : Moon, label: "Despejado", tone: day ? "sun" : "moon" };
  if (code <= 2) return { Icon: day ? CloudSun : CloudMoon, label: "Parcialmente nublado", tone: day ? "sun" : "moon" };
  if (code === 3) return { Icon: Cloud, label: "Nublado", tone: "cloud" };
  if (code === 45 || code === 48) return { Icon: CloudFog, label: "Niebla", tone: "cloud" };
  if (code >= 95) return { Icon: CloudLightning, label: "Tormenta", tone: "rain" };
  if ([71, 73, 75, 77, 85, 86].includes(code)) return { Icon: CloudSnow, label: "Nieve", tone: "rain" };
  if ([51, 53, 55, 56, 57].includes(code)) return { Icon: CloudDrizzle, label: "Llovizna", tone: "rain" };
  if ([61, 63, 65, 66, 67, 80, 81, 82].includes(code)) return { Icon: CloudRain, label: "Lluvia", tone: "rain" };
  return { Icon: CircleHelp, label: "Sin clasificar", tone: "cloud" };
}

export interface Plot { left: number; top: number; width: number; min: number; max: number }
export function WeatherRibbon({ data, plot }: { data: Environment | null; plot: Plot }) {
  const [selected, setSelected] = useState<number | null>(null);
  useEffect(() => { setSelected(null); }, [plot.min, plot.max]);
  useEffect(() => {
    const close = (event: PointerEvent) => { if (!(event.target as Element).closest('.weather-ribbon')) setSelected(null); };
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') setSelected(null); };
    document.addEventListener('pointerdown', close);
    document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('pointerdown', close); document.removeEventListener('keydown', escape); };
  }, []);
  if (!data || !plot.width) return null;
  const span = plot.max - plot.min;
  const visible = data.weather.filter(w => w.timestamp >= plot.min && w.timestamp <= plot.max);
  const daily = span > 3 * DAY;
  const capacity = Math.max(1, Math.floor(plot.width / (daily ? 88 : 72)));
  const edge = daily ? 34 : 18;
  const descriptions = new Map<number, string>();
  const candidates = daily ? data.days.flatMap(d => {
    const hours = visible.filter(w => w.timestamp >= d.start && w.timestamp < d.end);
    if (!hours.length) return [];
    const temperatures = hours.map(w => w.temperature).filter((n): n is number => n !== null);
    const codes = hours.map(w => w.code).filter((n): n is number => n !== null);
    const timestamp = hours[Math.floor(hours.length / 2)].timestamp;
    const code = codes.length ? Math.max(...codes) : null;
    const complete = hours.length === (d.end - d.start) / HOUR;
    const rain = hours.every(w => w.precipitation !== null) ? hours.reduce((sum, w) => sum + w.precipitation!, 0) : null;
    descriptions.set(timestamp, `${date(timestamp, { day: "numeric", month: "long" })} · ${condition(code, true).label}${temperatures.length ? ` · ${fmt(Math.min(...temperatures))}–${fmt(Math.max(...temperatures))} °C` : ""}${rain !== null ? ` · ${fmt(rain)} mm` : ""} · ${complete ? "Resumen del día" : "Día parcial"} · estimación Open-Meteo`);
    return [{ ...hours[0], timestamp, code, is_day: 1 }];
  }) : visible;
  // Keep icons anchored to actual weather timestamps; never stretch estimates into missing hours.
  const step = Math.max(HOUR, Math.ceil(span / capacity / HOUR) * HOUR);
  const items: WeatherHour[] = [];
  for (const w of candidates) {
    const x = (w.timestamp - plot.min) / span * plot.width;
    if (x < edge || x > plot.width - edge) continue;
    if (!items.length || w.timestamp - items[items.length - 1].timestamp >= step) items.push(w);
  }
  const active = items.find(w => w.timestamp === selected);
  const activeX = active ? (active.timestamp - plot.min) / span * plot.width : 0;
  return <div className="weather-ribbon" aria-label="Tiempo en Local por hora" style={{ left: plot.left, width: plot.width, top: 2 }}>
    <div className="weather-icons">
    {items.map(w => {
      const { Icon, label, tone } = condition(w.code, w.is_day === 1);
      const x = (w.timestamp - plot.min) / span * plot.width;
      const description = descriptions.get(w.timestamp) ?? `${date(w.timestamp)} · ${label} · Local ${fmt(w.temperature)} °C${w.precipitation === null ? "" : ` · ${fmt(w.precipitation)} mm en la hora anterior`} · estimación Open-Meteo`;
      return <button key={w.timestamp} className={`weather-mark ${tone}`} style={{ left: x }} aria-label={description} aria-expanded={selected === w.timestamp} aria-controls={selected === w.timestamp ? 'weather-detail' : undefined} onClick={() => setSelected(selected === w.timestamp ? null : w.timestamp)}>
        <Icon size={20} strokeWidth={1.6} />
        {span > 3 * DAY && <small>{date(w.timestamp, { day: "numeric", month: "short" })}</small>}
      </button>;
    })}
    </div>
    {active && <div id="weather-detail" className="weather-detail" role="status" style={{ left: Math.min(Math.max(0, activeX - 110), Math.max(0, plot.width - 220)) }}>
      <button className="weather-close" aria-label="Cerrar detalle del tiempo" onClick={() => setSelected(null)}><X size={15}/></button>
      <small>{date(active.timestamp)}</small>
      <strong>{condition(active.code, active.is_day === 1).label}</strong>
      {daily ? <p>{descriptions.get(active.timestamp)}</p> : <>
        <div className="weather-values"><b>{fmt(active.temperature)} °C</b><span>{fmt(active.precipitation)} mm<small>hora anterior</small></span></div>
        <small>Local · estimación Open-Meteo</small>
      </>}
    </div>}
  </div>;
}

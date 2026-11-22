import { useEffect, useRef, useState, type ReactNode } from "react";
import Highcharts from "highcharts/highstock";
import "highcharts/modules/accessibility";
import {
  Minus,
  Plus,
  Sun,
  Moon,
  RotateCw,
  ChevronLeft,
  ChevronRight,
  ChevronDown,
} from "lucide-react";
import {
  DAY,
  HOUR,
  date,
  fmt,
  get,
  heating,
  target,
  type Data,
  type Meta,
  type Range,
  type Source,
} from "../../lib/types";
import { useSessions, SessionDetail, euros } from "./Sessions";
import { WindowInsights } from "./Windows";
import { useDataRevision } from "../../lib/refresh";
import { installGestures } from "./gestures";
import { useEnvironment, WeatherRibbon, condition, daylightBands, type Plot } from "../environment/Weather";

Highcharts.setOptions({
  time: { timezone: "Europe/Madrid" },
  lang: { decimalPoint: ".", thousandsSep: "," },
});
interface Props {
  meta: Meta;
  room: number;
  source: Source;
  onView: (data: Data | null, range: Range) => void;
  children?: ReactNode;
}
const cache = new Map<string, { data: Data; time: number; bytes: number }>();
const ENERGY_COLOR = "#306f75";
const DAILY_COLUMN_WIDTH = DAY * .7;
function remember(key: string, data: Data) {
  cache.delete(key);
  cache.set(key, {
    data,
    time: Date.now(),
    bytes: JSON.stringify(data).length * 2,
  });
  let total = Array.from(cache.values()).reduce((n, e) => n + e.bytes, 0);
  while (cache.size > 6 || total > 8 * 1024 * 1024) {
    const k = cache.keys().next().value!;
    total -= cache.get(k)!.bytes;
    cache.delete(k);
  }
}

const RANGE_PRESETS = [
  { label: "6 h", duration: 6 * HOUR },
  { label: "24 h", duration: DAY },
  { label: "7 d", duration: 7 * DAY },
  { label: "30 d", duration: 30 * DAY },
];

function RangeControl({ range, bounds, onNavigate, onZoom }: {
  range: Range;
  bounds: Range;
  onNavigate: (min: number, max: number) => void;
  onZoom: (factor: number) => void;
}) {
  const control = useRef<HTMLDetailsElement>(null);
  const trigger = useRef<HTMLElement>(null);

  function close(restoreFocus: boolean) {
    if (!control.current?.open) return;
    control.current.open = false;
    if (restoreFocus) trigger.current?.focus({ preventScroll: true });
  }

  useEffect(() => {
    const onOutside = (event: PointerEvent) => {
      if (!control.current?.contains(event.target as Node)) close(false);
    };
    document.addEventListener("pointerdown", onOutside);
    return () => document.removeEventListener("pointerdown", onOutside);
  }, []);

  const span = range.max - range.min;
  const startDay = date(range.min, { day: "numeric", month: "short" });
  const endDay = date(range.max, { day: "numeric", month: "short" });
  const clock = { hour: "2-digit", minute: "2-digit" } as const;
  const count = span < HOUR ? span / 60_000 : span <= DAY ? span / HOUR : span / DAY;
  const unit = span < HOUR ? "min" : span <= DAY ? "h" : "d";
  const rounded = Math.round(count * 10) / 10;
  const duration = `${fmt(rounded, Number.isInteger(rounded) ? 0 : 1)} ${unit}`;
  const atStart = range.min <= bounds.min + 1000;
  const atEnd = range.max >= bounds.max - 1000;

  function apply(action: () => void) {
    action();
    close(true);
  }

  return (
    <details className="range-control" ref={control} onKeyDown={(event) => {
      if (event.key === "Escape" && control.current?.open) {
        event.stopPropagation();
        close(true);
      }
    }}>
      <summary ref={trigger} aria-label={`Visible interval: ${date(range.min)} to ${date(range.max)} (${duration})`}>
        <strong>{startDay === endDay ? startDay : `${startDay} – ${endDay}`}</strong>
        {span <= DAY && <span className="range-hours">{date(range.min, clock)} – {date(range.max, clock)}</span>}
        <span className="range-duration">{duration} <ChevronDown size={14} aria-hidden="true" /></span>
      </summary>
      <div className="range-panel">
        <div className="range-presets" role="group" aria-label="Interval duration">
          {RANGE_PRESETS.map(({ label, duration }) => (
            <button
              key={label}
              aria-pressed={Math.abs(span - duration) < 1000}
              disabled={duration > bounds.max - bounds.min + 1000}
              onClick={() => apply(() => onNavigate(range.max - duration, range.max))}
            >{label}</button>
          ))}
        </div>
        <div className="range-actions" role="group" aria-label="Pan and zoom">
          <button aria-label="Previous interval" title="Previous interval" disabled={atStart} onClick={() => apply(() => onNavigate(range.min - span, range.min))}><ChevronLeft size={17} /></button>
          <button aria-label="Next interval" title="Next interval" disabled={atEnd} onClick={() => apply(() => onNavigate(range.max, range.max + span))}><ChevronRight size={17} /></button>
          <button aria-label="Zoom out" title="Zoom out" disabled={atStart && atEnd} onClick={() => apply(() => onZoom(2))}><Minus size={16} /></button>
          <button aria-label="Zoom in" title="Zoom in" disabled={span <= 15 * 60_000} onClick={() => apply(() => onZoom(0.5))}><Plus size={16} /></button>
        </div>
      </div>
    </details>
  );
}

export function Timeline({ meta, room, source, onView, children }: Props) {
  const revision = useDataRevision();
  const previousEnd = useRef(meta.end);
  const host = useRef<HTMLDivElement>(null),
    chartRef = useRef<Highcharts.Chart | null>(null);
  const latest = useRef({ meta, room, source, onView });
  latest.current = { meta, room, source, onView };
  const loaded = useRef<Data | null>(null),
    loadRef = useRef<(r: Range, force?: boolean) => void>(() => {});
  const [range, setRange] = useState<Range>({
    min: meta.end - DAY,
    max: meta.end,
  });
  const { data: environment, failed: environmentFailed } = useEnvironment(source, range);
  const environmentRef = useRef(environment);
  environmentRef.current = environment;
  const [showOutdoor, setShowOutdoor] = useState(true);
  const [plot, setPlot] = useState<Plot & { height: number }>({ left: 0, top: 0, width: 0, height: 0, min: range.min, max: range.max });
  const sessions = useSessions(source, room, range, meta.updated);
  const sessionsRef = useRef(sessions.data);
  sessionsRef.current = sessions.data;
  const [selectedSession, setSelectedSession] = useState<string | null>(null);
  const [sessionExpanded, setSessionExpanded] = useState(false);
  function selectSession(id: string) {
    setSelectedSession(id);
    setSessionExpanded(true);
  }
  const [status, setStatus] = useState<"loading" | "ready" | "error">(
    "loading",
  );
  const [empty, setEmpty] = useState(false);
  const bounds = () => ({
    min: Math.min(latest.current.meta.start, latest.current.meta.end - DAY),
    max: latest.current.meta.end,
  });
  useEffect(() => {
    if (!host.current) return;
    let alive = true,
      controller: AbortController | undefined,
      timer: ReturnType<typeof setTimeout> | undefined;
    let wanted: Range = {
      min: latest.current.meta.end - DAY,
      max: latest.current.meta.end,
    };
    const notify = (r: Range) => {
      setRange(r);
      latest.current.onView(loaded.current, r);
    };
    const apply = (data: Data) => {
      const chart = chartRef.current;
      if (!chart || !alive) return;
      loaded.current = data;
      const temp: [number, number | null][] = data.samples.map((s) => [
        s[0],
        s[1],
      ]);
      for (const [start, end] of data.gaps)
        temp.push([(start + end) / 2, null]);
      for (const e of data.events.filter(
        (e) => e.series === "connected" && e.value === false,
      ))
        temp.push([(e.start + e.end) / 2, null]);
      temp.sort((a, b) => a[0] - b[0]);
      const targets: [number, number | null][] = [],
        demand: [number, number | null][] = [],
        known: [number, number | null][] = [];
      for (const series of ["settings", "callForHeat"]) {
        const intervals = data.events.filter((e) => e.series === series);
        intervals.forEach((e, i) => {
          const out = series === "settings" ? targets : demand;
          const v =
            series === "settings"
              ? target(e.value)
              : heating(e.value)
                ? 1
                : e.value === "NONE"
                  ? 0
                  : null;
          out.push([e.start, v], [e.end, v]);
          if (intervals[i + 1]?.start !== e.end) out.push([e.end, null]);
          if (series === "callForHeat" && v !== null)
            known.push([e.start, 0], [e.end, 0], [e.end, null]);
        });
      }
      chart.series[0].setData(temp, false, false, false);
      chart.series[1].setData(targets, false, false, false);
      chart.series[2].setData(demand, false, false, false);
      chart.series[4].setData(known, false, false, false);
      chart.redraw(false);
      setStatus("ready");
      notify({ min: chart.xAxis[0].min!, max: chart.xAxis[0].max! });
    };
    const load = (r: Range, force = false) => {
      wanted = r;
      clearTimeout(timer);
      const d = loaded.current,
        span = r.max - r.min,
        props = latest.current;
      const desiredBucket = Math.max(
        60_000,
        (span * 3) /
          Math.max(100, Math.round((chartRef.current?.plotWidth ?? 1000) / 4)),
      );
      if (
        !force &&
        d &&
        d.room === props.room &&
        d.source === props.source &&
        d.start <= r.min - span * 0.3 &&
        d.end >= r.max + span * 0.3 &&
        d.bucketMs <= desiredBucket * 1.8
      ) {
        controller?.abort();
        setStatus("ready");
        return;
      }
      timer = setTimeout(
        async () => {
          const b = bounds(),
            start =
              Math.floor(Math.max(b.min - DAY, r.min - span) / 60_000) * 60_000,
            end =
              Math.ceil(Math.min(b.max + DAY, r.max + span) / 60_000) * 60_000;
          const buckets = Math.max(
            100,
            Math.min(
              400,
              Math.round((chartRef.current?.plotWidth ?? 1000) / 4),
            ),
          );
          const key = `${props.source}:${props.room}:${start}:${end}:${buckets}`;
          controller?.abort();
          controller = new AbortController();
          const request = controller;
          setStatus("loading");
          try {
            const cached = cache.get(key);
            const data =
              !force && cached && Date.now() - cached.time < 60_000
                ? cached.data
                : await get<Data>(
                    `/api/series?source=${props.source}&room=${props.room}&start=${start}&end=${end}&buckets=${buckets}`,
                    request.signal,
                  );
            if (
              !alive ||
              request.signal.aborted ||
              latest.current.room !== props.room ||
              latest.current.source !== props.source
            )
              return;
            const axis = chartRef.current?.xAxis[0];
            if (axis && (data.start > axis.min! || data.end < axis.max!)) {
              // A slow response from a previous gesture must not replace the visible window.
              load({ min: axis.min!, max: axis.max! }, true);
              return;
            }
            remember(key, data);
            apply(data);
          } catch (e) {
            if (alive && !request.signal.aborted) setStatus("error");
          }
        },
        force ? 0 : 100,
      );
    };
    loadRef.current = load;
    const chart = Highcharts.stockChart(host.current, {
      chart: {
        backgroundColor: "transparent",
        animation: false,
        alignTicks: false,
        spacing: [36, 8, 10, 0],
        marginLeft: 46,
        style: { fontFamily: "inherit" },
        panning: { enabled: true, type: "x" },
        zooming: { pinchType: "x", mouseWheel: { enabled: false } },
        events: {
          render: function () {
            const el = this.container;
            el.dataset.min = String(this.xAxis[0].min);
            el.dataset.max = String(this.xAxis[0].max);
            const next = { left: this.plotLeft, top: this.plotTop, width: this.plotWidth, height: this.plotHeight, min: this.xAxis[0].min!, max: this.xAxis[0].max! };
            setPlot(previous => Object.keys(next).every(key => previous[key as keyof typeof next] === next[key as keyof typeof next]) ? previous : next);
          },
          click(e) { selectAt(e.chartX, e.chartY); },
        },
      },
      title: { text: undefined },
      credits: { enabled: false },
      navigator: { enabled: false },
      scrollbar: { enabled: false },
      rangeSelector: { enabled: false },
      legend: { enabled: false },
      responsive: {
        rules: [{ condition: { maxWidth: 600 }, chartOptions: { xAxis: { tickPixelInterval: 62 } } }],
      },
      accessibility: {
        description:
          `Temperature, target and heating demand history${source === "demo" ? ", with simulated consumption" : ""}. Use arrow keys to pan and plus or minus to zoom.`,
        keyboardNavigation: { enabled: false },
      },
      xAxis: {
        ordinal: false,
        min: wanted.min,
        max: wanted.max,
        minRange: 15 * 60_000,
        startOnTick: false,
        endOnTick: false,
        minPadding: 0,
        maxPadding: 0,
        overscroll: 0,
        lineColor: "#dce0e5",
        tickColor: "#dce0e5",
        tickPixelInterval: 95,
        crosshair: { width: 1, color: "#7a818d", dashStyle: "Dash" },
        labels: { style: { color: "#667080", fontSize: "12px" } },
        events: {
          afterSetExtremes(e) {
            if (!alive) return;
            const r = { min: e.min, max: e.max };
            notify(r);
            load(r);
          },
        },
      },
      yAxis: [
        {
          top: source === "demo" ? "0%" : "7%",
          height: source === "demo" ? "96%" : "89%",
          opposite: false,
          title: { text: undefined },
          labels: {
            formatter() { return fmt(Number(this.value), Number.isInteger(Number(this.value)) ? 0 : 1) + "°"; },
            style: { color: "#667080", fontSize: "12px", textOverflow: "clip", width: 46 },
            x: -8,
          },
          gridLineColor: "#47475319",
          tickAmount: 5,
          minPadding: 0.15,
          maxPadding: 0.15,
          showLastLabel: true,
        },
        { top: "96%", height: "4%", min: 0, max: 1, visible: false, offset: 0 },
        { visible: false, min: 0, max: 1 },
        {
          top: "0%", height: "96%", min: 0, opposite: true, offset: 0,
          title: { text: undefined }, gridLineWidth: 0, tickAmount: 4,
          visible: source === "demo",
          labels: { format: "{value} kWh", style: { fontSize: "11px", color: ENERGY_COLOR } },
        },
      ],
      tooltip: {
        followTouchMove: false,
        shared: false,
        animation: false,
        borderWidth: 0,
        backgroundColor: "#202631",
        borderRadius: 9,
        shadow: false,
        style: { color: "#fff", fontSize: "13px" },
        formatter() {
          if (this.series.options.id?.startsWith("energy-")) {
            const t = Number(this.x), data = sessionsRef.current;
            const daily = this.series.options.type === "column";
            const item = daily ? data?.days.find(d => (d.start+d.end)/2 === t) : data?.sessions.find(s => s.start <= t && s.end >= t);
            const energy = item?.energyKwh;
            const fraction = daily || !item ? 1 : (t - item.start) / (item.end - item.start);
            const value = energy ? `${fmt(energy.low * fraction)} – ${fmt(energy.high * fraction)} kWh` : "—";
            return `${date(t)}<br/><b>${value}</b><br/>${daily ? "Simulated consumption · visible part of the day" : "Simulated consumption · cumulative"}`;
          }
          const t = Number(this.x), env = environmentRef.current;
          const weather = env?.weather.find(w => w.timestamp <= t && t - w.timestamp < HOUR);
          const exterior = env?.outdoor.reduce<(number | null)[] | undefined>((best, row) =>
            Math.abs(row[0] - t) <= 7.5 * 60_000 && (!best || Math.abs(row[0] - t) < Math.abs(Number(best[0]) - t)) ? row : best, undefined);
          const event = loaded.current?.events.find(e => e.series === "settings" && e.start <= t && e.end > t);
          const demand = loaded.current?.events.find(e => e.series === "callForHeat" && e.start <= t && e.end > t);
          const exteriorValue = env?.firstOutdoor && t >= env.firstOutdoor ? exterior?.[1] : null;
          const label = this.series.options.id === "outdoor" ? "Entrance · outdoor" : "Indoor";
          let text = `<span style="color:#b7bfcb">${date(t)}</span><br/><b>${fmt(this.y)} °C</b> · ${label}`;
          if (this.series.options.id !== "outdoor") text += `<br/>Entrance: ${fmt(exteriorValue)} °C${exteriorValue == null ? "" : ` · ${date(Number(exterior![0]), { hour: "2-digit", minute: "2-digit" })}`}`;
          text += `<br/>Target: ${event ? (target(event.value) === null ? "Off" : fmt(target(event.value)) + " °C") : "—"}`;
          text += `<br/>Demand: ${!demand ? "—" : heating(demand.value) ? "Yes" : demand.value === "NONE" ? "No" : "—"}`;
          const session = sessionsRef.current?.sessions.find(s=>s.start <= t && s.end >= t);
          if (session) {
            text += `<br/>Home session: ${date(session.start,{hour:'2-digit',minute:'2-digit'})} – ${date(session.end,{hour:'2-digit',minute:'2-digit'})}`;
            if (session.energyKwh && session.cost) {
              const fraction = (t-session.start)/session.durationMs;
              text += `<br/>Simulated consumption: ${fmt(session.energyKwh.low*fraction)} – ${fmt(session.energyKwh.high*fraction)} kWh`;
              text += `<br/>Simulated cost: ${euros(session.cost.low*fraction)} – ${euros(session.cost.high*fraction)}`;
            }
          }
          if (weather) {
            text += `<br/><span style="color:#b7bfcb">Local · ${date(weather.timestamp, { hour: "2-digit", minute: "2-digit" })}</span><br/>${condition(weather.code, weather.is_day === 1).label} · ${fmt(weather.temperature)} °C`;
            if (weather.precipitation !== null) text += `<br/>Rain: ${fmt(weather.precipitation)} mm · previous hour`;
            text += '<br/><span style="color:#b7bfcb">Open-Meteo estimate</span>';
          }
          return text;
        },
      },
      plotOptions: {
        series: {
          animation: false,
          dataGrouping: { enabled: false },
          states: { hover: { lineWidthPlus: 0 }, inactive: { opacity: 1 } },
          turboThreshold: 10000,
          point: { events: { click(e) { selectAt(e.chartX, e.chartY, this.series.options.id?.startsWith("energy-")); } } },
        },
        line: { marker: { enabled: false }, connectNulls: false },
      },
      series: [
        {
          id: "temperature",
          type: "line",
          name: "Temperature",
          color: "#bd4c30",
          lineWidth: 2.7,
          zIndex: 3,
          data: [],
        },
        {
          id: "target",
          type: "line",
          name: "Target",
          color: "#697486",
          lineWidth: 1.5,
          dashStyle: "Dash",
          zIndex: 2,
          step: "left",
          enableMouseTracking: false,
          data: [],
        },
        {
          id: "heat", visible: false,
          type: "area",
          name: "Heating demand",
          yAxis: 1,
          color: "#e8805e",
          fillOpacity: 0.8,
          lineWidth: 0,
          threshold: 0,
          step: "left",
          enableMouseTracking: false,
          data: [],
        },
        {
          id: "bounds",
          type: "line",
          yAxis: 2,
          data: [
            [bounds().min, null],
            [bounds().max, null],
          ],
          enableMouseTracking: false,
          showInLegend: false,
          accessibility: { enabled: false },
        },
        {
          id: "known", visible: false,
          type: "line",
          yAxis: 1,
          name: "Known state",
          color: "#dce0e5",
          lineWidth: 2,
          enableMouseTracking: false,
          data: [],
          accessibility: { enabled: false },
        },
        {
          id: "outdoor", type: "spline", name: "Entrance · outdoor", color: "#3985a5",
          lineWidth: 2.5, marker: { enabled: false, states: { hover: { enabled: false } } },
          connectNulls: false, data: [], zIndex: 3,
        },
        { id: "house-known", name: "Known demand state", accessibility: { enabled: false }, type: "line", yAxis: 1, color: "#cdd4dc", lineWidth: 2, data: [], enableMouseTracking: false },
        { id: "house-heat", name: "Combined demand", type: "area", yAxis: 1, color: "#bd4c30", fillOpacity: .62, lineWidth: 0, step: "left", data: [], enableMouseTracking: false },
        { id: "energy-high", name: "Maximum simulated consumption", type: "line", yAxis: 3, color: ENERGY_COLOR, dashStyle: "ShortDash", lineWidth: 1.6, zIndex: 2, data: [], visible: false },
        { id: "energy-low", name: "Minimum simulated consumption", type: "line", yAxis: 3, color: ENERGY_COLOR, lineWidth: 1.6, zIndex: 2, data: [], visible: false },
        { id: "energy-day-high", name: "Maximum simulated daily consumption", type: "column", yAxis: 3, color: "#306f752d", grouping: false, pointRange: 0, pointPadding: 0, groupPadding: 0, borderWidth: 0, data: [], visible: false },
        { id: "energy-day-low", name: "Minimum simulated daily consumption", type: "column", yAxis: 3, color: "#306f755c", grouping: false, pointRange: 0, pointPadding: 0, groupPadding: 0, borderWidth: 0, data: [], visible: false },
      ],
    });
    chartRef.current = chart;
    function selectAt(x: number, y: number, energyPoint = false) {
      const c = chartRef.current, data = sessionsRef.current;
      if (!c || !data) return;
      const demandHit = y >= c.yAxis[1].toPixels(1) - 6;
      const t = c.xAxis[0].toValue(x);
      if (c.xAxis[0].max! - c.xAxis[0].min! > 3*DAY) {
        const day = data.days.find(d => d.start <= t && d.end > t);
        if (!day) return;
        // The temperature line's pointer target can overlap a column in the shared plot.
        const columnHit = day.energyKwh && y >= c.yAxis[3].toPixels(day.energyKwh.high)
          && Math.abs(t - (day.start + day.end) / 2) <= DAILY_COLUMN_WIDTH / 2;
        if (demandHit || energyPoint || columnHit) c.xAxis[0].setExtremes(day.start, day.end, true, false);
      } else if (demandHit || energyPoint) {
        const session = data.sessions.find(s => s.start <= t && s.end >= t);
        if (session) selectSession(session.id);
      }
    }
    const remove = installGestures(chart, bounds, selectAt);
    load(wanted, true);
    // Refresh from SQLite, never the Tado API, without moving the selected range.
    const refresh = setInterval(() => {
      if (document.visibilityState === "visible")
        load({ min: chart.xAxis[0].min!, max: chart.xAxis[0].max! }, true);
    }, 60_000);
    return () => {
      alive = false;
      clearTimeout(timer);
      clearInterval(refresh);
      controller?.abort();
      remove();
      chart.destroy();
      chartRef.current = null;
      loaded.current = null;
    };
  }, [source]);
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const data = sessions.data;
    const known: [number, number | null][] = [], heat: [number, number | null][] = [];
    const low: [number, number | null][] = [], high: [number, number | null][] = [];
    for (const [a,b] of data?.known ?? []) known.push([a,0],[b,0],[b,null]);
    for (const s of data?.sessions ?? []) {
      heat.push([s.start,1],[s.end,1],[s.end,null]);
      if (s.energyKwh) {
        low.push([s.start,0],[s.end,s.energyKwh.low],[s.end,null]);
        high.push([s.start,0],[s.end,s.energyKwh.high],[s.end,null]);
      }
    }
    const overview = range.max-range.min > 3*DAY;
    const update = (id: string, rows: [number, number | null][], visible = true) => {
      const series = chart.get(id) as Highcharts.Series;
      if (id.startsWith('energy-day')) {
        const pointRange = visible ? DAILY_COLUMN_WIDTH : 0;
        if ((series.options as Highcharts.SeriesColumnOptions).pointRange !== pointRange)
          series.update({ type: 'column', pointRange }, false);
      }
      series.setData(visible ? rows : [],false,false,false); series.setVisible(visible,false);
    };
    update('house-known', known); update('house-heat', heat);
    update('energy-low', low, source === 'demo' && !overview);
    update('energy-high', high, source === 'demo' && !overview);
    update('energy-day-low', (data?.days ?? []).map(d => [(d.start + d.end) / 2, d.energyKwh?.low ?? null]), source === 'demo' && overview);
    update('energy-day-high', (data?.days ?? []).map(d => [(d.start + d.end) / 2, d.energyKwh?.high ?? null]), source === 'demo' && overview);
    const axis = chart.xAxis[0];
    axis.removePlotBand('selected-session');
    const visible = data?.sessions.filter(s=>s.end>range.min && s.start<range.max) ?? [];
    const selected = visible.find(s=>s.id===selectedSession) ?? visible[visible.length-1];
    if (selected && !overview) axis.addPlotBand({id:'selected-session',from:selected.start,to:selected.end,color:'#de5b3812',zIndex:1});
    chart.redraw(false);
  }, [sessions.data, selectedSession, source, range.min, range.max]);
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    chart.series[5].setData((environment?.outdoor ?? []).map(s => [s[0], s[1]]), false, false, false);
    chart.series[5].setVisible(showOutdoor && source === "real", false);
    const axis = chart.xAxis[0];
    // Replacing only our own bands preserves the user's zoom and pan position.
    for (const band of [...(axis.options.plotBands ?? [])]) if (band.id?.startsWith("sun-")) axis.removePlotBand(band.id);
    for (const {from, to, daylight} of daylightBands(environment, range)) {
      axis.addPlotBand({ id: `sun-${from}`, from, to, color: daylight ? "#fbf0de" : "#e6eaf2", zIndex: 0 });
    }
    chart.redraw(false);
  }, [environment, range.min, range.max, showOutdoor, source]);
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    // Switching rooms keeps exactly the same window, but never labels old room data as the new room.
    loaded.current = null;
    for (const i of [0, 1, 2, 4]) chart.series[i].setData([], false);
    chart.redraw(false);
    const r = { min: chart.xAxis[0].min!, max: chart.xAxis[0].max! };
    latest.current.onView(null, r);
    loadRef.current(r, true);
  }, [room, source]);
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    cache.clear();
    loadRef.current({min: chart.xAxis[0].min!, max: chart.xAxis[0].max!}, true);
  }, [revision, meta.updated]);
  useEffect(() => {
    const c = chartRef.current;
    const oldEnd = previousEnd.current;
    previousEnd.current = meta.end;
    if (!c) return;
    const axis = c.xAxis[0];
    const pinnedToNow = Math.abs(axis.max! - oldEnd) < 1000;
    const span = axis.max! - axis.min!;
    c.series[3].setData([[bounds().min, null], [bounds().max, null]], false);
    if (pinnedToNow && meta.end !== oldEnd)
      axis.setExtremes(meta.end-span, meta.end, true, false, {trigger:'fresh-data'});
    else c.redraw(false);
  }, [meta.end]);
  useEffect(() => {
    const d = loaded.current;
    setEmpty(
      status === "ready" &&
        !!d &&
        !d.samples.some((s) => s[0] >= range.min && s[0] <= range.max),
    );
  }, [range, status]);
  const navigate = (min: number, max: number) => {
    const b = bounds(),
      span = Math.min(b.max - b.min, Math.max(15 * 60_000, max - min));
    min = Math.max(b.min, Math.min(min, b.max - span));
    chartRef.current?.xAxis[0].setExtremes(min, min + span, true, false, {
      trigger: "control",
    });
  };
  const zoom = (factor: number) => {
    const half = ((range.max - range.min) * factor) / 2,
      mid = (range.max + range.min) / 2;
    navigate(mid - half, mid + half);
  };
  const lastOutdoor = environment?.outdoor.filter(s => s[0] >= range.min && s[0] <= range.max).at(-1);
  return (
    <section className="timeline" aria-label="Interactive history">
      <RangeControl range={range} bounds={bounds()} onNavigate={navigate} onZoom={zoom} />
      {source === "real" && <div className="environment-status" aria-live="polite">
        {environmentFailed ? "Could not refresh outdoor context" :
          environment?.hueStatus && (!environment.hueStatus.ok || Date.now() - (environment.hueStatus.updated ?? 0) > 15 * 60_000) ? "No recent entrance sensor connection" :
          environment && (!environment.firstOutdoor ? "Waiting for the first entrance reading" :
          range.max < environment.firstOutdoor ? `Entrance: history since ${date(environment.firstOutdoor)}` : null)}
        {environment?.weatherStatus && !environment.weatherStatus.ok && <span>Weather not refreshed</span>}
      </div>}
      <div
        className="plot-shell"
        tabIndex={0}
        role="region"
        aria-label="Chart. Arrow keys: pan. Plus and minus: zoom."
        onKeyDown={(e) => {
          if (e.target !== e.currentTarget) return;
          if (["ArrowLeft", "ArrowRight", "+", "-", "="].includes(e.key))
            e.preventDefault();
          const width = range.max - range.min;
          if (e.key === "ArrowLeft")
            navigate(range.min - width * 0.25, range.max - width * 0.25);
          if (e.key === "ArrowRight")
            navigate(range.min + width * 0.25, range.max + width * 0.25);
          if (e.key === "+" || e.key === "=") zoom(0.5);
          if (e.key === "-") zoom(2);
        }}
      >
        <div ref={host} className="chart" />
        <WindowInsights key={`${source}:${room}`} source={source} room={room} range={range} plot={plot} updated={meta.updated} />
        {source === "demo" && <span className="landscape-demo">Simulated data</span>}
        <WeatherRibbon data={environment} plot={plot} />
        {plot.width > 0 && daylightBands(environment, {min: plot.min, max: plot.max}).map(({from, to, daylight}) => {
          const span = plot.max - plot.min;
          const width = (to - from) / span * plot.width;
          if (width < 30) return null;
          const Icon = daylight ? Sun : Moon;
          const label = daylight ? "Day" : "Night";
          return <span
            key={from}
            className={`light-marker ${daylight ? "day" : "night"}`}
            style={{ left: plot.left + ((from + to) / 2 - plot.min) / span * plot.width, top: plot.top + 6 }}
          >
            <Icon size={14} aria-hidden="true" />
            <span className={width < 84 ? "sr-only" : undefined}>{label}</span>
          </span>;
        })}
        {empty && (
          <div className="empty-plot">
            No readings in this interval
            <span>Pan to the available history.</span>
          </div>
        )}
        {status === "error" && (
          <div className="chart-error" role="alert">
            Could not load this interval.
            <button onClick={() => loadRef.current(range, true)}>
              <RotateCw size={14} />
              Retry
            </button>
          </div>
        )}
      </div>
      <div className="legend">
        <span>
          <i className="line actual" />
          Indoor
        </span>
        <span>
          <i className="line target" />
          Target
        </span>
        <span>
          <i className="heat" />
          Home demand
        </span>
        {source === "demo" && <span><i className="line" style={{ background: ENERGY_COLOR }} />Simulated consumption</span>}
        {source === "real" && <button className={"outdoor-toggle " + (showOutdoor ? "active" : "")} aria-pressed={showOutdoor} onClick={() => setShowOutdoor(v => !v)} title="Show measured entrance temperature">
          <i className="line exterior" />Outdoor{lastOutdoor && <strong>{fmt(lastOutdoor[1])}°</strong>}
        </button>}
      </div>
      <span className="sr-only" role="status">{status === "loading" ? "Loading interval…" : ""}</span>
      {children}
      <SessionDetail
        data={sessions.data}
        range={range}
        selectedId={selectedSession}
        expanded={sessionExpanded}
        onExpandedChange={setSessionExpanded}
        onSelect={selectSession}
        onZoom={navigate}
        roomName={meta.rooms.find(r => r.id === room)?.name ?? ""}
        failed={sessions.failed}
        retry={sessions.retry}
      />
    </section>
  );
}

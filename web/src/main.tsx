import { useCallback, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Thermometer,
  Flame,
  Droplets,
  Clock3,
  ArrowDownRight,
  ArrowUpRight,
  Sofa,
  BedDouble,
  Blocks,
  Monitor,
} from "lucide-react";
import { Invoices } from "./features/heating/Sessions";
import { Timeline } from "./features/heating/Timeline";
import { RefreshMenu } from "./features/RefreshMenu";
import {
  date,
  fmt,
  get,
  type Data,
  type Meta,
  type Range,
} from "./lib/types";
import "./style.css";
import { DATA_UPDATED, useAutoRefresh, useDataRevision } from "./lib/refresh";

const appName = document.querySelector<HTMLMetaElement>('meta[name="apple-mobile-web-app-title"]')?.content || "Casa";
const appIcon = document.querySelector<HTMLLinkElement>('link[rel="icon"]')?.getAttribute("href") || "/favicon.svg";

function duration(ms: number) {
  const minutes = Math.round(ms / 60_000);
  return minutes < 60
    ? `${minutes} min`
    : `${Math.floor(minutes / 60)} h ${minutes % 60 ? `${minutes % 60} min` : ""}`;
}
function App() {
  const [meta, setMeta] = useState<Meta | null>(null),
    [room, setRoom] = useState(1),
    [error, setError] = useState("");
  const [view, setView] = useState<{ data: Data | null; range: Range }>({
    data: null,
    range: { min: 0, max: 0 },
  });
  const onView = useCallback(
    (data: Data | null, range: Range) => setView({ data, range }),
    [],
  );
  const [retry, setRetry] = useState(0);
  useAutoRefresh();
  const revision = useDataRevision();
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    const refresh = async () => {
      try {
        const next = await get<Meta>(
          "/api/meta",
          controller.signal,
        );
        if (active) {
          setMeta(next);
          setError("");
          setRoom(current => next.rooms.some(r => r.id === current) ? current : next.rooms[0]?.id ?? 1);
        }
      } catch (e) {
        if (active)
          setError(
            "Cannot read local history. Check that the service is available.",
          );
      }
    };
    setMeta(null);
    setView((v) => ({ ...v, data: null }));
    refresh();
    window.addEventListener(DATA_UPDATED, refresh);
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") refresh();
    }, 60_000);
    return () => {
      active = false;
      window.removeEventListener(DATA_UPDATED, refresh);
      controller.abort();
      clearInterval(timer);
    };
  }, [retry]);
  const selected = meta?.rooms.find((r) => r.id === room);
  const [stats, setStats] = useState<{
    room: number;
    start: number;
    end: number;
    count: number;
    min: number | null;
    max: number | null;
    change: number | null;
    heatMs: number;
    knownMs: number;
    overshoot: number;
    changes: { start: number; target: number | null }[];
  } | null>(null);
  const [statsError, setStatsError] = useState(false);
  useEffect(() => {
    setStats(null);
    setStatsError(false);
    if (!meta || view.range.max <= view.range.min) return;
    const controller = new AbortController();
    const timer = setTimeout(
      () =>
        get<NonNullable<typeof stats>>(
          `/api/summary?room=${room}&start=${Math.floor(view.range.min)}&end=${Math.ceil(view.range.max)}`,
          controller.signal,
        )
          .then(setStats)
          .catch(() => {
            if (!controller.signal.aborted) setStatsError(true);
          }),
      180,
    );
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [room, view.range.min, view.range.max, meta?.updated, revision]);
  const valid = stats?.room === room ? stats : null;
  const min = valid?.min ?? null,
    max = valid?.max ?? null,
    heatMs = valid?.heatMs ?? 0,
    knownMs = valid?.knownMs ?? 0,
    changes = valid?.changes ?? [],
    observedChange = valid?.change ?? null;

  return (
    <div className="app">
      <header className="topbar">
        <a className="brand" href="/" aria-label={`${appName}, home`}>
          <img className="brand-mark" src={appIcon} width={28} height={28} alt="" />
          <span className="brand-title">{appName}</span>
        </a>
        <div className="header-right">
          <RefreshMenu />
        </div>
      </header>
      <div className="workspace">
        <nav className="room-nav" aria-label="Rooms">
            {meta?.rooms.map((item) => (
              <button
                className={"room " + (item.id === room ? "selected" : "")}
                key={item.id}
                aria-pressed={item.id === room}
                onClick={() => setRoom(item.id)}
              >
                <span className="room-icon">
                  {item.name === "Living room" ? <Sofa size={18}/> : item.name === "Bedroom" ? <BedDouble size={18}/> : item.name === "Room 2" ? <Blocks size={18}/> : <Monitor size={18}/>}
                </span>
                <span className="room-title">
                  {item.name}
                </span>
                <strong>
                  {fmt(item.temperature)}
                  <span>°</span>
                </strong>
              </button>
            ))}
        </nav>
        <main>
          {error ? (
            <div className="error" role="alert">
              {error}
              <button onClick={() => setRetry((n) => n + 1)}>Retry</button>
            </div>
          ) : !meta ? (
            <div className="initial-loading">
              <div className="skeleton title" />
              <div className="skeleton graph" />
              <p>Loading home history…</p>
            </div>
          ) : !selected ? (
            <p className="no-changes">No room history has been collected yet.</p>
          ) : (
            <>
              <div className="page-heading">
                <div className="room-heading" key={room}>
                  <h1>{selected?.name ?? "Heating"}</h1>
                  <div className="freshness">
                    <Clock3 size={14} />
                    <span>
                      {selected?.observed ? `Latest reading · ${date(selected.observed)}` : "No readings"}
                    </span>
                  </div>
                </div>
                <div className="current-reading" key={`reading-${room}`}>
                  <div>
                    <span>{fmt(selected?.temperature)}</span>
                    <small>°C</small>
                  </div>
                  <p aria-label={`Humidity: ${selected?.humidity == null ? "no reading" : fmt(selected.humidity, 0) + " %"}`}>
                    <Droplets size={13} />
                    {selected?.humidity == null ? "—" : `${fmt(selected.humidity, 0)} %`}
                  </p>
                </div>
              </div>
              <Timeline
                meta={meta}
                room={room}
                onView={onView}
              >
              <section
                className="observations"
                aria-label="Interval summary"
              >
                {statsError && <p role="status" className="summary-error">Summary unavailable</p>}
                <div className="metrics">
                  <div className="metric">
                    <span>
                      <Thermometer size={16} />
                      Min. — max.
                    </span>
                    <strong>
                      {fmt(min)}
                      <small> — </small>
                      {fmt(max)}
                      <small> °C</small>
                    </strong>
                  </div>
                  <div className="metric">
                    <span>
                      <Flame size={16} />
                      Room demand
                    </span>
                    <strong>{knownMs ? duration(heatMs) : "—"}</strong>
                    <p>
                      {knownMs
                        ? `Of ${duration(knownMs)} with a known state`
                        : "No known state in this interval"}
                    </p>
                  </div>
                  <div className="metric">
                    <span>
                      {observedChange !== null && observedChange < 0 ? (
                        <ArrowDownRight size={17} />
                      ) : (
                        <ArrowUpRight size={17} />
                      )}
                      Change
                    </span>
                    <strong>
                      {observedChange === null
                        ? "—"
                        : (observedChange > 0 ? "+" : "") + fmt(observedChange)}
                      <small> °C</small>
                    </strong>
                  </div>
                </div>
              </section>
              </Timeline>
              <div className="support-grid">
                <section className="activity">
                  <div className="section-title">
                    <h2>Target changes</h2>
                    <span>{changes.length}</span>
                  </div>
                  {changes.length ? (
                    <ul>
                      {changes
                        .slice(-5)
                        .reverse()
                        .map((e, i) => (
                          <li key={e.start + ":" + i}>
                            <time>
                              {date(e.start, {
                                day: "numeric",
                                month: "short",
                                hour: "2-digit",
                                minute: "2-digit",
                              })}
                            </time>
                            <strong>
                              {e.target === null
                                ? "Off"
                                : fmt(e.target) + " °C"}
                            </strong>
                          </li>
                        ))}
                    </ul>
                  ) : (
                    <div className="no-changes">
                      <Clock3 size={20} />
                      <p>No changes in this interval.</p>
                    </div>
                  )}
                  {changes.length > 5 && (
                    <p className="muted">
                      Showing the five most recent changes.
                    </p>
                  )}
                </section>
                <Invoices historyStart={meta.start} historyEnd={meta.end} />
              </div>

            </>
          )}
        </main>
      </div>
    </div>
  );
}
createRoot(document.getElementById("root")!).render(<App />);

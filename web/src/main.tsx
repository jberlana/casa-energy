import { useCallback, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  House,
  Thermometer,
  Flame,
  ChevronDown,
  Activity,
  Beaker,
  Droplets,
  Clock3,
  ArrowDownRight,
  ArrowUpRight,
  Sofa,
  BedDouble,
  Blocks,
  Monitor,
} from "lucide-react";
import { Invoices } from "./Sessions";
import { Timeline } from "./Timeline";
import {
  date,
  fmt,
  get,
  type Data,
  type Meta,
  type Range,
  type Source,
} from "./types";
import "./style.css";
import { DATA_UPDATED, useAutoRefresh, useDataRevision } from "./refresh";

function duration(ms: number) {
  const minutes = Math.round(ms / 60_000);
  return minutes < 60
    ? `${minutes} min`
    : `${Math.floor(minutes / 60)} h ${minutes % 60 ? `${minutes % 60} min` : ""}`;
}
function App() {
  const [source, setSource] = useState<Source>("demo"),
    [meta, setMeta] = useState<Meta | null>(null),
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
  useAutoRefresh(source);
  const revision = useDataRevision();
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    const refresh = async () => {
      try {
        const next = await get<Meta>(
          `/api/meta?source=${source}`,
          controller.signal,
        );
        if (active) {
          setMeta(next);
          setError("");
          if (!next.rooms.some((r) => r.id === room))
            setRoom(next.rooms[0]?.id ?? 1);
        }
      } catch (e) {
        if (active)
          setError(
            "No se puede leer el histórico local. Comprueba que el servicio esté disponible.",
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
  }, [source, retry]);
  const selected = meta?.rooms.find((r) => r.id === room);
  const [stats, setStats] = useState<{
    source: Source;
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
          `/api/summary?source=${source}&room=${room}&start=${Math.floor(view.range.min)}&end=${Math.ceil(view.range.max)}`,
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
  }, [room, source, view.range.min, view.range.max, meta?.updated, revision]);
  const valid = stats?.room === room && stats.source === source ? stats : null;
  const min = valid?.min ?? null,
    max = valid?.max ?? null,
    heatMs = valid?.heatMs ?? 0,
    knownMs = valid?.knownMs ?? 0,
    changes = valid?.changes ?? [],
    observedChange = valid?.change ?? null;

  return (
    <div className="app">
      <header className="topbar">
        <a className="brand" href="/" aria-label="Casa, inicio">
          <span className="brand-mark">
            <House size={22} strokeWidth={1.8} />
          </span>
          Casa
        </a>
        <div className="header-right">
          <label
            className={"source-switch " + (source === "demo" ? "demo" : "")}
          >
            <span className="sr-only">Origen de los datos</span>
            {source === "demo" ? <Beaker size={15} /> : <Activity size={15} />}
            <select
              value={source}
              onChange={(e) => setSource(e.target.value as Source)}
            >
              <option value="real">Mi casa</option>
              <option value="demo">Demo · 6 meses</option>
            </select>
            <ChevronDown size={14} />
          </label>
        </div>
      </header>
      {source === "demo" && (
        <div className="demo-banner">
          <Beaker size={16} />
          <strong>Datos simulados</strong>
        </div>
      )}
      <div className="workspace">
        <nav className="room-nav" aria-label="Habitaciones">
            {meta?.rooms.map((item) => (
              <button
                className={"room " + (item.id === room ? "selected" : "")}
                key={item.id}
                aria-pressed={item.id === room}
                onClick={() => setRoom(item.id)}
              >
                <span className="room-icon">
                  {item.name === "Salón" ? <Sofa size={18}/> : item.name === "Dormitorio" ? <BedDouble size={18}/> : item.name === "Habitación 2" ? <Blocks size={18}/> : <Monitor size={18}/>}
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
              <button onClick={() => setRetry((n) => n + 1)}>Reintentar</button>
            </div>
          ) : !meta ? (
            <div className="initial-loading">
              <div className="skeleton title" />
              <div className="skeleton graph" />
              <p>Cargando el historial de casa…</p>
            </div>
          ) : (
            <>
              <div className="page-heading">
                <div className="room-heading" key={room}>
                  <h1>{selected?.name ?? "Calefacción"}</h1>
                  <div className="freshness">
                    <Clock3 size={14} />
                    <span>
                      {selected?.observed ? `Última lectura · ${date(selected.observed)}` : "Sin lecturas"}
                    </span>
                  </div>
                </div>
                <div className="current-reading" key={`reading-${room}`}>
                  <div>
                    <span>{fmt(selected?.temperature)}</span>
                    <small>°C</small>
                  </div>
                  <p aria-label={`Humedad: ${selected?.humidity == null ? "sin lectura" : fmt(selected.humidity, 0) + " %"}`}>
                    <Droplets size={13} />
                    {selected?.humidity == null ? "—" : `${fmt(selected.humidity, 0)} %`}
                  </p>
                </div>
              </div>
              <Timeline
                key={source}
                meta={meta}
                room={room}
                source={source}
                onView={onView}
              >
              <section
                className="observations"
                aria-label="Resumen del intervalo"
              >
                {statsError && <p role="status" className="summary-error">Resumen no disponible</p>}
                <div className="metrics">
                  <div className="metric">
                    <span>
                      <Thermometer size={16} />
                      Mín. — máx.
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
                      Demanda de la habitación
                    </span>
                    <strong>{knownMs ? duration(heatMs) : "—"}</strong>
                    <p>
                      {knownMs
                        ? `De ${duration(knownMs)} con estado conocido`
                        : "Sin estado conocido en este tramo"}
                    </p>
                  </div>
                  <div className="metric">
                    <span>
                      {observedChange !== null && observedChange < 0 ? (
                        <ArrowDownRight size={17} />
                      ) : (
                        <ArrowUpRight size={17} />
                      )}
                      Variación
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
                    <h2>Cambios de objetivo</h2>
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
                                ? "Apagado"
                                : fmt(e.target) + " °C"}
                            </strong>
                          </li>
                        ))}
                    </ul>
                  ) : (
                    <div className="no-changes">
                      <Clock3 size={20} />
                      <p>Sin cambios en este intervalo.</p>
                    </div>
                  )}
                  {changes.length > 5 && (
                    <p className="muted">
                      Se muestran los cinco más recientes.
                    </p>
                  )}
                </section>
                {source === "real" && <Invoices historyStart={meta.start} historyEnd={meta.end} />}
              </div>

            </>
          )}
        </main>
      </div>
    </div>
  );
}
createRoot(document.getElementById("root")!).render(<App />);

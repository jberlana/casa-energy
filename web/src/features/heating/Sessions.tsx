import { useDataRevision } from "../../lib/refresh";
import { useEffect, useState } from "react";
import { Flame, ReceiptText, ChevronDown, Maximize2 } from "lucide-react";
import { DAY, HOUR, date, fmt, get, type Range } from "../../lib/types";

const demandLabels = { LOW: "Low", MEDIUM: "Medium", HIGH: "High", HEATING: "Level unavailable" } as const;
const roomList = new Intl.ListFormat("en");

export interface Session {
  id: string; start: number; end: number; durationMs: number; visibleMs: number;
  partialStart: boolean; partialEnd: boolean;
  rooms: { id: number; name: string; demandMs: number }[];
  demandSegments: { roomId: number; start: number; end: number; level: keyof typeof demandLabels }[];
  response: { first: [number, number] | null; last: [number, number] | null; delta: number | null; complete: boolean };
}
export interface Sessions {
  room: number; start: number; end: number;
  sessions: Session[]; known: [number, number][];
  days: { start: number; end: number; demandMs: number; knownMs: number }[];
}
const cache = new Map<string, { data: Sessions; at: number; bytes: number }>();
export function useSessions(room: number, range: Range, updated: number | null) {
  const revision = useDataRevision();
  const [data, setData] = useState<Sessions | null>(null);
  useEffect(() => { cache.clear(); }, [updated, revision]);
  const [failed, setFailed] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setFailed(false);
    const timer = setTimeout(async () => {
      const url = `/api/sessions?room=${room}&start=${Math.floor(range.min)}&end=${Math.ceil(range.max)}`;
      try {
        const cached = cache.get(url);
        const next = cached && Date.now()-cached.at < 55_000 ? cached.data : await get<Sessions>(url, controller.signal);
        if (controller.signal.aborted) return;
        cache.set(url, { data: next, at: Date.now(), bytes: JSON.stringify(next).length*2 });
        while (cache.size > 4 || [...cache.values()].reduce((n,v)=>n+v.bytes,0) > 4*1024*1024) cache.delete(cache.keys().next().value!);
        setData(next);
      } catch { if (!controller.signal.aborted) setFailed(true); }
    }, 220);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [room, range.min, range.max, updated, retry, revision]);
  return { data: data?.room === room ? data : null, failed, retry: () => setRetry(v => v+1) };
}
export function elapsed(ms: number) {
  if (ms > 0 && ms < 60_000) return "<1 min";
  const mins = Math.round(ms/60_000);
  return mins < 60 ? `${mins} min` : `${Math.floor(mins/60)} h${mins%60 ? ` ${mins%60} min` : ''}`;
}
const clock = (t: number) => date(t, { hour: '2-digit', minute: '2-digit' });
export const euros = (n: number) => `${fmt(n, 2)} €`;

const dayKey = (t: number) => date(t, { day: "numeric", month: "numeric", year: "numeric" });
const shortDay = (t: number) => date(t, { day: "numeric", month: "short" });

function SessionRow({ session, open, onToggle, onZoom, roomName }: {
  session: Session; open: boolean; onToggle: () => void;
  onZoom: (start: number, end: number) => void; roomName: string;
}) {
  const roomNames = new Map(session.rooms.map(room => [room.id, room.name]));
  const initial = session.partialStart ? [] : session.demandSegments.filter(segment => segment.start === session.start);
  const commonLevel = initial.every(segment => segment.level === initial[0]?.level) ? initial[0]?.level : undefined;
  const firstRooms = roomList.format(initial.map(({ roomId, level }) => {
    const name = roomNames.get(roomId) ?? "Room";
    return commonLevel ? name : `${name} (${demandLabels[level].toLowerCase()})`;
  }));
  const initialLevel = commonLevel === "HEATING" ? "Demand level unavailable" : commonLevel && `${demandLabels[commonLevel]} at start`;
  const end = dayKey(session.start) === dayKey(session.end) ? clock(session.end) : `${shortDay(session.end)} ${clock(session.end)}`;
  const detailsId = `session-changes-${session.id}`;
  const response = session.response.complete ? session.response.delta : null;

  return <div className="session-row">
    <button className="session-toggle" type="button" aria-expanded={open} aria-controls={open ? detailsId : undefined} onClick={onToggle}>
      <span className="session-time">{clock(session.start)}–{end}</span>
      <strong className="session-duration">{elapsed(session.durationMs)}</strong>
      <ChevronDown size={16} aria-hidden="true" />
      <span className="session-origin">
        <span>{initial.length ? `${firstRooms} called for heat first` : "Start unconfirmed"}</span>
        {initialLevel && <span className="session-initial-level">{initialLevel}</span>}
        {session.partialEnd && <span>End unconfirmed</span>}
      </span>
    </button>
    {open && <div className="session-changes" id={detailsId}>
      <ol className="session-events" aria-label="Demand changes">
        {session.demandSegments.map(segment => <li key={`${segment.roomId}-${segment.start}`}>
          <time dateTime={new Date(segment.start).toISOString()}>{clock(segment.start)}</time>
          <span>{roomNames.get(segment.roomId)}</span>
          <span className="session-event-level">{demandLabels[segment.level]} <span>· {elapsed(segment.end - segment.start)}</span></span>
        </li>)}
        <li>
          <time dateTime={new Date(session.end).toISOString()}>{clock(session.end)}</time>
          <span className="session-end">{session.partialEnd ? "End unconfirmed" : "Demand ended"}</span>
        </li>
      </ol>
      <div className="session-actions">
        {response !== null && <p>Response in {roomName} · <strong>{response > 0 ? "+" : ""}{fmt(response, 2)} °C</strong></p>}
        <button className="text-button" onClick={() => onZoom(session.start - HOUR, session.end + HOUR)}><Maximize2 size={14} /> Zoom to session</button>
      </div>
    </div>}
  </div>;
}

export function SessionDetail({ data, range, selectedId, expanded, onToggle, onZoom, roomName, failed, retry }: {
  data: Sessions | null; range: Range; selectedId: string | null;
  expanded: boolean;
  onToggle: (id: string) => void; onZoom: (a: number, b: number) => void;
  roomName: string; failed: boolean; retry: () => void;
}) {
  const current = data && data.start <= Math.floor(range.min) && data.end >= Math.ceil(range.max);
  const visible = data?.sessions.filter(s => s.end > range.min && s.start < range.max) ?? [];
  const latest = visible.at(-1);
  const overview = range.max - range.min > 3 * DAY;
  const groups = new Map<string, Session[]>();
  if (current && !overview) {
    for (const session of [...visible].reverse()) {
      const key = dayKey(session.start);
      const group = groups.get(key);
      if (group) group.push(session);
      else groups.set(key, [session]);
    }
  }
  return <section className="session-section" aria-label="Demand sessions">
    <div className="section-title"><h2><Flame size={16} /> Heating sessions</h2>{current && visible.length > 1 && <span>{visible.length} en el intervalo</span>}</div>
    {failed ? <p role="alert">Could not load sessions. <button className="text-button" onClick={retry}>Retry</button></p> : !current ?
      <p className="session-empty">Loading sessions…</p> : !latest ?
      <p className="session-empty">{data.known.length ? 'No demand recorded in this interval.' : 'Insufficient coverage to identify sessions.'}</p> : overview ?
      <div className="session-overview"><strong>{elapsed(data.days.reduce((n,d)=>n+d.demandMs,0))}</strong><span>of combined demand</span><button onClick={() => onZoom(latest.start-HOUR, latest.end+HOUR)}>Explore the latest session</button></div> :
      Array.from(groups, ([day, sessions]) => <div className="session-day" key={day}>
        <h3>{shortDay(sessions[0].start)}</h3>
        {sessions.map(session => <SessionRow key={session.id} session={session} open={expanded && selectedId === session.id}
          onToggle={() => onToggle(session.id)} onZoom={onZoom} roomName={roomName} />)}
      </div>)}
  </section>;
}

interface Invoice {
  period_start: string; period_end: string; billed_days: number;
  consumption_kwh: string; consumption_m3: string; fixed_eur: string;
  variable_eur: string; other_eur: string; tax_eur: string; total_eur: string;
}
const dayLabel = (day: string) => date(Date.parse(day+'T12:00:00+01:00'),{day:'numeric',month:'short',year:'numeric'});
export function Invoices({ historyStart, historyEnd }: { historyStart: number; historyEnd: number }) {
  const revision = useDataRevision();
  const [invoices,setInvoices] = useState<Invoice[] | null>(null);
  const [failed,setFailed] = useState(false);
  const [retry,setRetry] = useState(0);
  useEffect(()=>{
    const controller=new AbortController();
    setFailed(false);
    get<{invoices:Invoice[]}>('/api/invoices',controller.signal).then(r=>{if(!controller.signal.aborted)setInvoices(r.invoices);}).catch(()=>{if(!controller.signal.aborted)setFailed(true);});
    return ()=>controller.abort();
  },[retry, revision]);
  return <details className="invoices" aria-label="Verified gas invoices">
    <summary className="section-title"><h2><ReceiptText size={17}/> Gas invoices</h2><span>{invoices ? `${invoices.length} · Octopus` : 'Octopus'} <ChevronDown size={15}/></span></summary>
    {failed ? <p role="alert">Could not load invoices. <button className="text-button" onClick={()=>setRetry(v=>v+1)}>Retry</button></p> : invoices === null ? <p>Loading invoices…</p> : !invoices.length ? <p>No verified invoices yet.</p> : <>
      <div className="invoice-list">{invoices.map(b=>{
        const overlap = b.period_start <= new Intl.DateTimeFormat("sv-SE", {timeZone:"Europe/Madrid"}).format(historyEnd) && b.period_end >= new Intl.DateTimeFormat("sv-SE", {timeZone:"Europe/Madrid"}).format(historyStart);
        const parts = [['Consumption',b.variable_eur],['Fixed',b.fixed_eur],['Other',b.other_eur],['Taxes',b.tax_eur]] as const;
        return <details className="invoice-row" key={b.period_start}>
          <summary><span className="invoice-period">{dayLabel(b.period_start)} – {dayLabel(b.period_end)}<small>{b.billed_days} days · {overlap ? 'Coverage comparison pending' : 'No matching Tado history'}</small></span><span className="invoice-quantity">{fmt(Number(b.consumption_kwh),2)}<small> kWh</small></span><strong>{euros(Number(b.total_eur))}</strong><ChevronDown size={16}/></summary>
          <div className="invoice-breakdown"><div className="invoice-bar" aria-hidden="true">{parts.map(([label,amount],i)=><i key={label} className={`part-${i}`} style={{width:`${Number(amount)/Number(b.total_eur)*100}%`}}/>)}</div>
            <dl>{parts.map(([label,amount],i)=><div key={label}><dt><i className={`part-${i}`}/>{label}</dt><dd>{euros(Number(amount))}</dd></div>)}</dl>
            <p>{fmt(Number(b.consumption_m3),0)} m³ billed. Consumption may include hot water and other uses. Fixed and other charges are not attributed to sessions.</p>
          </div>
        </details>;
      })}</div><p className="invoice-note">Imported from PDFs · partial history, no automatic refresh.</p>
    </>}
  </details>;
}

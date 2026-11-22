import { useDataRevision } from "../../lib/refresh";
import { useEffect, useState } from "react";
import { Flame, ReceiptText, ChevronDown, Maximize2 } from "lucide-react";
import { DAY, date, fmt, get, type Range, type Source } from "../../lib/types";

type Estimate = { low: number; high: number } | null;

export interface Session {
  id: string; start: number; end: number; durationMs: number; visibleMs: number;
  partialStart: boolean; partialEnd: boolean;
  rooms: { id: number; name: string; demandMs: number }[];
  response: { first: [number, number] | null; last: [number, number] | null; delta: number | null; complete: boolean };
  energyKwh: Estimate;
  cost: Estimate;
}
export interface Sessions {
  source: Source; room: number; start: number; end: number;
  sessions: Session[]; known: [number, number][];
  days: { start: number; end: number; demandMs: number; knownMs: number; energyKwh: Estimate; cost: Estimate }[];
  costModel: 'illustration' | 'uncalibrated';
}
const cache = new Map<string, { data: Sessions; at: number; bytes: number }>();
export function useSessions(source: Source, room: number, range: Range, updated: number | null) {
  const revision = useDataRevision();
  const [data, setData] = useState<Sessions | null>(null);
  useEffect(() => { cache.clear(); }, [updated, revision]);
  const [failed, setFailed] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setFailed(false);
    const timer = setTimeout(async () => {
      const url = `/api/sessions?source=${source}&room=${room}&start=${Math.floor(range.min)}&end=${Math.ceil(range.max)}`;
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
  }, [source, room, range.min, range.max, updated, retry, revision]);
  return { data: data?.room === room && data.source === source ? data : null, failed, retry: () => setRetry(v => v+1) };
}
export function elapsed(ms: number) {
  const mins = Math.round(ms/60_000);
  return mins < 60 ? `${mins} min` : `${Math.floor(mins/60)} h${mins%60 ? ` ${mins%60} min` : ''}`;
}
const clock = (t: number) => date(t, { hour: '2-digit', minute: '2-digit' });
export const euros = (n: number) => `${fmt(n, 2)} €`;

export function SessionDetail({ data, range, selectedId, expanded, onExpandedChange, onSelect, onZoom, roomName, failed, retry }: {
  data: Sessions | null; range: Range; selectedId: string | null;
  expanded: boolean; onExpandedChange: (expanded: boolean) => void;
  onSelect: (id: string) => void; onZoom: (a: number, b: number) => void;
  roomName: string; failed: boolean; retry: () => void;
}) {
  const current = data && data.start <= Math.floor(range.min) && data.end >= Math.ceil(range.max);
  const visible = data?.sessions.filter(s => s.end > range.min && s.start < range.max) ?? [];
  const selected = visible.find(s => s.id === selectedId) ?? visible[visible.length-1];
  const overview = range.max-range.min > 3*DAY;
  return <section className="session-section" aria-label="Demand sessions">
    <div className="section-title"><h2><Flame size={16} /> Heating sessions</h2><span>{failed ? 'Unavailable' : !current ? 'Refreshing…' : `${visible.length} in this interval`}</span></div>
    {failed ? <p role="alert">Could not read sessions. <button className="text-button" onClick={retry}>Retry</button></p> : !current ?
      <p className="session-empty">Calculating sessions for this interval…</p> : !selected ?
      <p className="session-empty">{data.known.length ? 'No demand recorded in this interval.' : 'Not enough state data to identify sessions.'}</p> : overview ?
      <div className="session-overview"><strong>{elapsed(data.days.reduce((n,d)=>n+d.demandMs,0))}</strong><span>of combined demand · overlaps count once</span><button onClick={() => onZoom(selected.start-3_600_000, selected.end+3_600_000)}>Explore the latest session</button></div> : <>
      <div className="session-picker" role="group" aria-label="Choose a session">
        {visible.map(s => <button key={s.id} aria-pressed={s.id === selected.id} onClick={() => onSelect(s.id)}>
          <span>{date(s.start, {day:'numeric',month:'short'})}</span>{clock(s.start)} – {clock(s.end)}
        </button>)}
      </div>
      <details className="session-inspector" open={expanded} onToggle={e => onExpandedChange(e.currentTarget.open)}>
        <summary className="session-summary"><span><strong>{elapsed(selected.durationMs)}</strong> of combined demand</span><span>Details <ChevronDown size={14}/></span></summary>
        <div className="session-facts">
          <div><span>Combined demand</span><strong>{elapsed(selected.durationMs)}</strong><small>{selected.partialStart || selected.partialEnd ? 'Incomplete interval · boundaries unconfirmed' : 'Start and end observed'}</small></div>
          <div><span>Response in {roomName}</span><strong>{selected.response.delta == null ? '—' : `${selected.response.delta > 0 ? '+' : ''}${fmt(selected.response.delta,2)} °C`}</strong><small>{selected.response.first && selected.response.last ? `${fmt(selected.response.first[1])}° (${clock(selected.response.first[0])}) → ${fmt(selected.response.last[1])}° (${clock(selected.response.last[0])})` : 'No readings during this session'}</small>{!selected.response.complete && <small>Insufficient readings or gaps</small>}{!selected.rooms.some(r=>r.id===data.room) && <small>No heat requested during this session</small>}</div>
          <div className="session-cost"><span>{data.source === 'demo' ? 'Simulated cost' : 'Session cost'}</span><strong>{selected.cost ? `${fmt(selected.cost.low,2)}–${fmt(selected.cost.high,2)} €` : '—'}</strong><small>{selected.cost ? 'Illustrative variable consumption' : 'No matching invoice for calibration'}</small></div>
        </div>
        <details className="room-demands"><summary>Rooms that requested heat <ChevronDown size={15} /></summary>
          <ul>{selected.rooms.map(r=><li key={r.id}><span>{r.name}</span><strong>{elapsed(r.demandMs)}</strong></li>)}</ul>
          <p>Room durations overlap. Demand does not confirm burner operation or consumption.</p>
        </details>
        <button className="text-button" onClick={()=>onZoom(selected.start-3_600_000,selected.end+3_600_000)}><Maximize2 size={14}/> Zoom to session</button>
      </details>
    </>}
    {data?.source === 'demo' && <details className="estimate-method"><summary>How cost is simulated</summary><p>Illustrative example: 8–12 kW of gas input during demand, at €0.08/kWh. It does not use your tariff or a calibrated boiler model. Fixed charges and taxes are excluded. The range represents these assumptions, not statistical confidence.</p></details>}
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

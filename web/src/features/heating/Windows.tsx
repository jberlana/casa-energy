import { useEffect, useRef, useState } from 'react';
import { Wind, X } from 'lucide-react';
import { date, fmt, get, type Range } from '../../lib/types';
import { useDataRevision } from '../../lib/refresh';

type Label = 'yes' | 'no' | 'unknown';
interface WindowEvent {
  id: string; start: number; end: number; onset: [number, number]; dropC: number | null;
  origins: string[]; reasons: string[]; alternatives: string[];
  label: Label | null; provisional: boolean; demand: boolean | null;
  confirmedWindow?: [number, number];
}
interface WindowEvents {
  room: number; start: number; end: number; events: WindowEvent[];
  askForFeedback: boolean; reviewedCount: number;
}
interface Plot extends Range { left: number; top: number; width: number; height: number }
const clock = (t: number) => date(t, {hour: '2-digit', minute: '2-digit'});
const title = (e: WindowEvent) => e.label === 'yes' ? 'Confirmed by you' : e.label === 'no' ? 'Not ventilation' :
  e.origins.includes('tado') ? 'Detected by Tado' : 'Possible ventilation';

export function WindowInsights({room, range, plot, updated}: {
  room: number; range: Range; plot: Plot; updated: number | null;
}) {
  const revision = useDataRevision();
  const [data, setData] = useState<WindowEvents | null>(null);
  const [failed, setFailed] = useState(false);
  const [reload, setReload] = useState(0);
  const [selection, setSelection] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState(false);
  const panel = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(() => {
      setFailed(false);
      get<WindowEvents>(`/api/window-events?room=${room}&start=${Math.floor(range.min)}&end=${Math.ceil(range.max)}`, controller.signal)
        .then(result => { if (!controller.signal.aborted) setData(result); })
        .catch(() => { if (!controller.signal.aborted) setFailed(true); });
    }, 250);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [room, range.min, range.max, updated, revision, reload]);
  useEffect(() => { setSelection(null); setEditing(false); setSaveError(false); }, [room]);
  useEffect(() => { if (selection) panel.current?.focus({preventScroll: true}); }, [selection]);
  const current = data?.room === room && data.start <= Math.floor(plot.min) && data.end >= Math.ceil(plot.max) ? data : null;
  const visible = current?.events.filter(e => e.end >= plot.min && e.start <= plot.max) ?? [];
  const selected = visible.find(e => e.id === selection);
  const groups: {x: number; events: WindowEvent[]}[] = [];
  for (const event of visible) {
    const x = Math.max(22, Math.min(plot.width - 22, (Math.max(plot.min, event.start) - plot.min) / (plot.max - plot.min) * plot.width));
    const previous = groups.at(-1);
    if (previous && x - previous.x < 44) previous.events.push(event);
    else groups.push({x, events: [event]});
  }
  function close() {
    setSelection(null); setEditing(false); setSaveError(false);
    trigger.current?.focus({preventScroll: true});
  }
  async function answer(event: WindowEvent, label: Label) {
    setSaving(true); setSaveError(false);
    try {
      const response = await fetch('/api/window-feedback', {
        method: 'POST', headers: {'Content-Type': 'application/json', 'X-Casa-Feedback': '1'},
        signal: AbortSignal.timeout(15000),
        body: JSON.stringify({room, eventId: event.id, start: event.start, end: event.end, label}),
      });
      if (!response.ok) throw new Error('feedback');
      setData(previous => previous && ({...previous,
        askForFeedback: previous.reviewedCount + (event.label ? 0 : 1) < 3,
        reviewedCount: previous.reviewedCount + (event.label ? 0 : 1),
        events: previous.events.map(e => e.id === event.id ? {...e, label} : e)}));
      setEditing(false);
      setReload(value => value + 1);
    } catch { setSaveError(true); }
    finally { setSaving(false); }
  }
  return <>
    {plot.width > 0 && groups.map(group => <button
      key={group.events[0].id} className={`window-marker ${group.events.every(e => e.label === 'no') ? 'dismissed' : ''}`}
      style={{left: plot.left + group.x - 22, top: plot.top + plot.height - 60}}
      title={group.events.length > 1 ? `${group.events.length} possible ventilation events` : title(group.events[0])}
      aria-label={group.events.length > 1 ? `${group.events.length} ventilation events` : `${title(group.events[0])}, ${date(group.events[0].start)}`}
      onClick={e => { trigger.current = e.currentTarget; setSelection(group.events[0].id); setEditing(false); setSaveError(false); }}>
      <Wind size={17} aria-hidden="true" />{group.events.length > 1 && <small>{group.events.length}</small>}
    </button>)}
    {failed && <button className="window-retry" aria-label="Retry ventilation analysis" title="Could not analyze ventilation" onClick={() => setReload(v => v + 1)}><Wind size={15}/>?</button>}
    {selected && <div className="window-detail" ref={panel} tabIndex={-1} role="dialog" aria-modal="false" aria-label="Ventilation details"
      onKeyDown={e => { if (e.key === 'Escape') {e.stopPropagation(); close();} }}>
      <div className="window-heading"><strong><Wind size={17}/>{title(selected)}</strong><button aria-label="Close ventilation details" onClick={close}><X size={18}/></button></div>
      <p className="window-time">{date(selected.start, {day:'numeric',month:'short'})} · {selected.confirmedWindow ? `${clock(selected.confirmedWindow[0])} – ${clock(selected.confirmedWindow[1])} · recorded` : selected.onset[0] === selected.onset[1] ? clock(selected.start) : `Between ${clock(selected.onset[0])} and ${clock(selected.onset[1])}`}</p>
      {selected.dropC !== null && <p className="window-drop">−{fmt(selected.dropC, 2)} °C <span>observed drop</span></p>}
      <details><summary>View evidence</summary>
        <ul>{selected.reasons.map(reason => <li key={reason}>{reason}</li>)}</ul>
        {selected.alternatives.length > 0 && <><span>Other explanations</span><ul>{selected.alternatives.map(reason => <li key={reason}>{reason}</li>)}</ul></>}
        <p className="window-note">{selected.demand === true ? 'There was heating demand. ' : ''}{selected.origins.includes('guided') ? 'Opening recorded during a guided trial.' : 'The end of cooling does not confirm when the window was closed.'}</p>
      </details>
      {(editing || (current?.askForFeedback && !selected.label)) ? <div className="window-feedback">
        <span>Was a window opened?</span><div role="group" aria-label="Confirm ventilation">
          {([['yes', 'Yes'], ['no', 'No'], ['unknown', 'Unknown']] as const).map(([value, label]) =>
            <button key={value} disabled={saving} aria-pressed={selected.label === value} onClick={() => answer(selected, value)}>{label}</button>)}
        </div></div> : <button className="text-button" onClick={() => setEditing(true)}>{selected.label ? 'Correct' : 'Review detection'}</button>}
      {saveError && <p role="alert">Could not save. Try again.</p>}
      {visible.length > 1 && <div className="window-navigation">
        <button disabled={visible.indexOf(selected) === 0} onClick={() => {setSelection(visible[visible.indexOf(selected) - 1].id); setEditing(false);}}>Previous</button>
        <span>{visible.indexOf(selected) + 1} / {visible.length}</span>
        <button disabled={visible.indexOf(selected) === visible.length - 1} onClick={() => {setSelection(visible[visible.indexOf(selected) + 1].id); setEditing(false);}}>Next</button>
      </div>}
    </div>}
  </>;
}

import { useEffect, useRef, useState } from 'react';
import { Ellipsis, RefreshCw } from 'lucide-react';
import { usePullRefresh } from './usePullRefresh';
import { DATA_UPDATED } from '../lib/refresh';
import { date } from '../lib/types';

type Provider = 'tado' | 'hue' | 'octopus';
type Target = Provider | 'all';
interface State {
  running: boolean; revision: number;
  providers: Record<Provider, {state: string; at?: number; lastSuccess?: number | null}>;
  request?: {provider: Target; accepted: boolean; reason?: string; retryAt?: number; decisions?: Partial<Record<Provider, {accepted: boolean; reason?: string}>>};
}
const providers: [Target, string][] = [['tado', 'Tado'], ['hue', 'Hue'], ['octopus', 'Octopus'], ['all', 'all']];
const labels: Record<string, string> = {
  running: 'Refreshing…', updated: 'Updated', failed: 'Could not refresh. You can try again.',
  unavailable: 'Check the provider connection.', busy: 'Another refresh is in progress.',
  quota: 'Request limit reached. Try again later.', cooldown: 'Wait before refreshing again.',
  interrupted: 'The refresh was interrupted.', pending_review: 'Documents available · amounts awaiting verification',
  partial: 'Documents downloaded · more remain for the next refresh',
};

export function RefreshMenu() {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<State | null>(null);
  const [pending, setPending] = useState<Target | null>(null);
  const [message, setMessage] = useState<{provider?: Target; text: string; transient?: boolean} | null>(null);
  const [blocked, setBlocked] = useState<Partial<Record<Provider, string>>>({});
  const [pullMessage, setPullMessage] = useState('');
  const pullActive = useRef(false);
  const skipped = useRef(false);
  const inFlight = useRef(false);
  const [attempt, setAttempt] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const items = useRef<(HTMLButtonElement | null)[]>([]);
  const revision = useRef<number | null>(null);
  const active = useRef(true);
  const request = useRef<AbortController | null>(null);
  useEffect(() => { active.current = true; return () => {active.current = false; request.current?.abort();}; }, []);
  useEffect(() => {
    if (!open) return;
    const frame = requestAnimationFrame(() => items.current.find(item => item && !item.disabled)?.focus());
    const outside = (e: PointerEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false); };
    const escape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {e.preventDefault(); setOpen(false); button.current?.focus();}
    };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener('pointerdown', outside);
      document.removeEventListener('keydown', escape);
    };
  }, [open]);
  function accept(next: State) {
    if (revision.current !== null && revision.current !== next.revision) window.dispatchEvent(new Event(DATA_UPDATED));
    revision.current = next.revision;
    setState(next);
  }
  useEffect(() => {
    if (!open && !state?.running && !pending) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const response = await fetch('/api/refresh', {signal: AbortSignal.any([controller.signal, AbortSignal.timeout(10_000)])});
        if (!response.ok) throw new Error('status');
        const next: State = await response.json();
        if (controller.signal.aborted) return;
        accept(next);
        setMessage(previous => previous?.transient ? null : previous);
      } catch {
        if (!controller.signal.aborted) setMessage({text: 'Could not check refresh status.', transient: true});
      }
      if (!controller.signal.aborted) timer = setTimeout(poll, 2000);
    };
    void poll();
    return () => {controller.abort(); clearTimeout(timer);};
  }, [open, state?.running, pending, attempt]);

  async function refresh(provider: Target) {
    if (inFlight.current || pending || state?.running) return;
    inFlight.current = true;
    if (provider === 'all') {pullActive.current = true; skipped.current = false; setPullMessage('Refreshing providers…');}
    setPending(provider); setMessage(null); setBlocked({});
    request.current = new AbortController();
    try {
      const response = await fetch(`/api/refresh/${provider}`, {
        method: 'POST', headers: {'X-Casa-Refresh': '1'},
        signal: AbortSignal.any([request.current.signal, AbortSignal.timeout(10_000)]),
      });
      if (!response.ok) throw new Error('refresh');
      const next: State = await response.json();
      if (!active.current) return;
      accept(next);
      if (provider === 'all') {
        setBlocked(Object.fromEntries(Object.entries(next.request?.decisions ?? {}).filter(([, d]) => !d.accepted).map(([name, d]) => [name, labels[d.reason ?? 'failed'] ?? labels.failed])));
        skipped.current = !next.request?.accepted || Object.values(next.request?.decisions ?? {}).some(d => !d.accepted);
      }
      if (next.request && !next.request.accepted) {
        const retry = next.request.retryAt ? ` Available at ${date(next.request.retryAt, {hour:'2-digit',minute:'2-digit'})}.` : '';
        setMessage({provider, text: provider === 'all' && !next.request.reason ? 'No providers refreshed. See individual status.' : (labels[next.request.reason ?? 'failed'] ?? labels.failed) + retry});
      }
      if (!next.running && next.request?.accepted) window.dispatchEvent(new Event(DATA_UPDATED));
    } catch {
      if (active.current) {skipped.current = true; setMessage({provider, text: 'Could not confirm the request. Checking its status…', transient: true});}
    } finally {
      inFlight.current = false;
      if (active.current) {setPending(null); setAttempt(v => v + 1);}
    }
  }
  useEffect(() => {
    if (!pullActive.current || pending || state?.running) return;
    pullActive.current = false;
    const incomplete = skipped.current || !state || Object.values(state.providers).some(p =>
      !['updated', 'pending_review'].includes(p.state));
    setPullMessage(incomplete ? 'Refresh incomplete. Check the menu.' : 'Refresh complete');
  }, [pending, state]);
  useEffect(() => {
    if (!pullMessage || pullActive.current) return;
    const timer = setTimeout(() => setPullMessage(''), 5000);
    return () => clearTimeout(timer);
  }, [pullMessage]);
  const distance = usePullRefresh(!open && !pending && !state?.running, () => void refresh('all'));
  const disabled = !!pending || !!state?.running;
  return <><div className="refresh-menu" ref={root} onKeyDown={e => {
    if (open && ['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) {
      e.preventDefault();
      const enabled = items.current.filter((item): item is HTMLButtonElement => !!item && !item.disabled);
      const at = enabled.indexOf(document.activeElement as HTMLButtonElement);
      const index = e.key === 'Home' ? 0 : e.key === 'End' ? enabled.length-1 : at < 0 ? (e.key === 'ArrowUp' ? enabled.length-1 : 0) : (at + (e.key === 'ArrowUp' ? -1 : 1) + enabled.length) % enabled.length;
      enabled[index]?.focus();
    }
  }}>
    <button ref={button} className="refresh-menu-trigger" aria-label="Data actions" aria-expanded={open} aria-haspopup="menu"
      onKeyDown={e => {if (!open && ['ArrowDown', 'ArrowUp'].includes(e.key)) {e.preventDefault(); setOpen(true);}}}
      onClick={() => setOpen(v => !v)}><Ellipsis size={22}/>{(pending || state?.running) && <span className="refresh-activity"/>}</button>
    {open && <div className="refresh-menu-panel" role="menu" aria-label="Refresh data" onBlur={e => {
      if (e.relatedTarget && !root.current?.contains(e.relatedTarget as Node)) setOpen(false);
    }}>
      <span className="refresh-menu-title">Refresh data</span>
      {providers.map(([provider, label], i) => {
        const providerState = provider === 'all' ? null : state?.providers?.[provider];
        const status = providerState?.state;
        const last = providerState?.lastSuccess;
        const detail = message?.provider === provider ? message.text : provider !== 'all' && blocked[provider] ? blocked[provider] : pending === provider || (pending === 'all' && status === 'running') ? labels.running : labels[status ?? 'idle'];
        return <button key={provider} role="menuitem" ref={el => {items.current[i] = el;}} disabled={disabled} onClick={() => void refresh(provider)}>
          <RefreshCw size={15} className={status === 'running' || pending === provider ? 'refresh-spinning' : ''}/>
          <span>Refresh {label}{detail && <small aria-live="polite">{detail}</small>}{provider !== 'all' && <small className="refresh-last">{last ? <>Last refreshed <time dateTime={new Date(last).toISOString()}>{date(last)}</time></> : state ? 'No successful refresh recorded' : 'Checking last refresh…'}</small>}</span>
        </button>;
      })}
      {message && !message.provider && <p role="status">{message.text}</p>}
    </div>}
  </div>{!open && (distance > 0 || pullMessage) && <div className="pull-refresh-status" role="status">
    <RefreshCw size={16} className={pending || state?.running ? 'refresh-spinning' : ''}/>
    <span>{distance > 0 ? distance >= 64 ? 'Release to refresh all' : 'Pull to refresh all' : pullMessage}</span>
  </div>}</>;
}

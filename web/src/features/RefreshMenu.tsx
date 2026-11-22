import { useEffect, useRef, useState } from 'react';
import { Ellipsis, RefreshCw } from 'lucide-react';
import { DATA_UPDATED } from '../lib/refresh';
import type { Source } from '../lib/types';

type Provider = 'tado' | 'hue' | 'octopus';
interface State {
  running: boolean; revision: number;
  providers: Record<Provider, {state: string; at?: number}>;
  request?: {provider: Provider; accepted: boolean; reason?: string; retryAt?: number};
}
const providers: [Provider, string][] = [['tado', 'Tado'], ['hue', 'Hue'], ['octopus', 'Octopus']];
const labels: Record<string, string> = {
  running: 'Actualizando…', updated: 'Actualizado', failed: 'No se pudo actualizar. Puedes reintentarlo.',
  unavailable: 'Revisa la vinculación del proveedor.', busy: 'Hay otra actualización en curso.',
  quota: 'Límite de consultas alcanzado. Inténtalo más tarde.', cooldown: 'Espera antes de volver a actualizar.',
  interrupted: 'La actualización se interrumpió.', pending_review: 'Documentos disponibles · importes pendientes de verificar',
  partial: 'Documentos descargados · quedan más para la próxima actualización',
};

export function RefreshMenu({source}: {source: Source}) {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<State | null>(null);
  const [pending, setPending] = useState<Provider | null>(null);
  const [message, setMessage] = useState<{provider?: Provider; text: string; transient?: boolean} | null>(null);
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
    if (source !== 'real' || (!open && !state?.running && !pending)) return;
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
        if (!controller.signal.aborted) setMessage({text: 'No se pudo comprobar la actualización.', transient: true});
      }
      if (!controller.signal.aborted) timer = setTimeout(poll, 2000);
    };
    void poll();
    return () => {controller.abort(); clearTimeout(timer);};
  }, [source, open, state?.running, pending, attempt]);

  async function refresh(provider: Provider) {
    if (source !== 'real' || pending || state?.running) return;
    setPending(provider); setMessage(null);
    request.current = new AbortController();
    try {
      const response = await fetch(`/api/refresh/${provider}?source=real`, {
        method: 'POST', headers: {'X-Casa-Refresh': '1'},
        signal: AbortSignal.any([request.current.signal, AbortSignal.timeout(10_000)]),
      });
      if (!response.ok) throw new Error('refresh');
      const next: State = await response.json();
      if (!active.current) return;
      accept(next);
      if (next.request && !next.request.accepted) {
        const retry = next.request.retryAt ? ` Disponible a las ${new Date(next.request.retryAt).toLocaleTimeString('es-ES', {hour:'2-digit',minute:'2-digit'})}.` : '';
        setMessage({provider, text: (labels[next.request.reason ?? 'failed'] ?? labels.failed) + retry});
      }
      if (!next.running && next.request?.accepted) window.dispatchEvent(new Event(DATA_UPDATED));
    } catch {
      if (active.current) setMessage({provider, text: 'No se pudo confirmar la solicitud. Comprobando su estado…', transient: true});
    } finally {
      if (active.current) {setPending(null); setAttempt(v => v + 1);}
    }
  }
  const disabled = source !== 'real' || !!pending || !!state?.running;
  return <div className="refresh-menu" ref={root} onKeyDown={e => {
    if (open && ['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) {
      e.preventDefault();
      const enabled = items.current.filter((item): item is HTMLButtonElement => !!item && !item.disabled);
      const at = enabled.indexOf(document.activeElement as HTMLButtonElement);
      const index = e.key === 'Home' ? 0 : e.key === 'End' ? enabled.length-1 : at < 0 ? (e.key === 'ArrowUp' ? enabled.length-1 : 0) : (at + (e.key === 'ArrowUp' ? -1 : 1) + enabled.length) % enabled.length;
      enabled[index]?.focus();
    }
  }}>
    <button ref={button} className="refresh-menu-trigger" aria-label="Acciones de datos" aria-expanded={open} aria-haspopup="menu"
      onKeyDown={e => {if (!open && ['ArrowDown', 'ArrowUp'].includes(e.key)) {e.preventDefault(); setOpen(true);}}}
      onClick={() => setOpen(v => !v)}><Ellipsis size={22}/>{(pending || state?.running) && <span className="refresh-activity"/>}</button>
    {open && <div className="refresh-menu-panel" role="menu" aria-label="Actualizar datos" onBlur={e => {
      if (e.relatedTarget && !root.current?.contains(e.relatedTarget as Node)) setOpen(false);
    }}>
      <span className="refresh-menu-title">Actualizar datos</span>
      {providers.map(([provider, label], i) => {
        const status = state?.providers?.[provider]?.state;
        const detail = message?.provider === provider ? message.text : pending === provider ? labels.running : labels[status ?? 'idle'];
        return <button key={provider} role="menuitem" ref={el => {items.current[i] = el;}} disabled={disabled} onClick={() => void refresh(provider)}>
          <RefreshCw size={15} className={status === 'running' || pending === provider ? 'refresh-spinning' : ''}/>
          <span>Actualizar {label}{source === 'real' && detail && <small aria-live="polite">{detail}</small>}</span>
        </button>;
      })}
      {source === 'demo' && <p>Selecciona «Mi casa» para actualizar los proveedores.</p>}
      {source === 'real' && message && !message.provider && <p role="status">{message.text}</p>}
    </div>}
  </div>;
}

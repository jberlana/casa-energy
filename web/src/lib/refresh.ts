import { useEffect, useState } from 'react';

export const DATA_UPDATED = 'casa-data-updated';
export function useDataRevision() {
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const update = () => setRevision(v => v+1);
    window.addEventListener(DATA_UPDATED, update);
    return () => window.removeEventListener(DATA_UPDATED, update);
  }, []);
  return revision;
}

// The server owns staleness, quota and concurrency decisions, shared by every browser.
export function useAutoRefresh() {
  useEffect(() => {
    let stopped = false, running = false, busy = false, revision: number | null = null;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    const check = async () => {
      clearTimeout(timer);
      if (stopped || document.visibilityState !== 'visible' || busy) return;
      busy = true;
      try {
        const result = await fetch('/api/refresh', {
          method: running ? 'GET' : 'POST',
          headers: running ? {} : { 'X-Casa-Refresh': '1' },
          signal: AbortSignal.any([controller.signal, AbortSignal.timeout(10_000)]),
        });
        if (!result.ok) throw new Error('refresh unavailable');
        const state: {running: boolean; revision: number} = await result.json();
        if (stopped) return;
        if (revision !== state.revision) window.dispatchEvent(new Event(DATA_UPDATED));
        revision = state.revision;
        running = state.running;
      } catch { /* Keep the current history. Retry on the next bounded check. */ }
      finally {
        busy = false;
        if (!stopped && document.visibilityState === 'visible') timer = setTimeout(check, running ? 2000 : 60_000);
      }
    };
    const visible = () => {
      clearTimeout(timer);
      if (document.visibilityState === 'visible') {
        window.dispatchEvent(new Event(DATA_UPDATED));
        void check();
      }
    };
    document.addEventListener('visibilitychange', visible);
    window.addEventListener('pageshow', visible);
    void check();
    return () => {
      stopped = true; controller.abort(); clearTimeout(timer);
      document.removeEventListener('visibilitychange', visible);
      window.removeEventListener('pageshow', visible);
    };
  }, []);
}

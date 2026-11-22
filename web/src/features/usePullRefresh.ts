import { useEffect, useRef, useState } from 'react';

// Start at the page top, outside controls and the chart. Never claim a pinch or horizontal pan.
export function usePullRefresh(enabled: boolean, refresh: () => void) {
  const [distance, setDistance] = useState(0);
  const callback = useRef(refresh);
  callback.current = refresh;
  useEffect(() => {
    if (!enabled) {setDistance(0); return;}
    let start: {x: number; y: number} | null = null;
    let pulled = 0;
    const reset = () => {start = null; pulled = 0; setDistance(0);};
    const begin = (e: TouchEvent) => {
      reset();
      if (e.touches.length !== 1 || window.scrollY > 0 || !(e.target instanceof Element) ||
          e.target.closest('button, a, input, select, textarea, summary, [role="menu"], .refresh-menu, [role="region"], [data-no-pull]')) return;
      start = {x: e.touches[0].clientX, y: e.touches[0].clientY};
    };
    const move = (e: TouchEvent) => {
      if (!start) return;
      if (e.touches.length !== 1 || window.scrollY > 0) {reset(); return;}
      const dx = e.touches[0].clientX-start.x, dy = e.touches[0].clientY-start.y;
      if (dy < -5 || Math.abs(dx) > Math.max(12, dy)) {reset(); return;}
      if (dy < 8) {pulled = 0; setDistance(0); return;}
      if (!e.cancelable) {reset(); return;}
      e.preventDefault();
      pulled = Math.min(100, dy * .5);
      setDistance(pulled);
    };
    const end = () => {const armed = pulled >= 64; reset(); if (armed) callback.current();};
    document.addEventListener('touchstart', begin, {passive: true});
    document.addEventListener('touchmove', move, {passive: false});
    document.addEventListener('touchend', end);
    document.addEventListener('touchcancel', reset);
    document.documentElement.classList.add('pull-refresh-enabled');
    return () => {
      document.removeEventListener('touchstart', begin); document.removeEventListener('touchmove', move);
      document.removeEventListener('touchend', end); document.removeEventListener('touchcancel', reset);
      document.documentElement.classList.remove('pull-refresh-enabled');
    };
  }, [enabled]);
  return distance;
}

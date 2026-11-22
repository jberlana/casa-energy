import type Highcharts from "highcharts/highstock";
import { type Range } from "../../lib/types";

// Touch handling belongs to the chart, independent of React renders and fetches.
// Horizontal gestures are captured; vertical scrolling remains the browser's job.
export function installGestures(
  chart: Highcharts.Chart,
  current: () => Range,
  changeRange: (range: Range) => void,
  onTap?: (x: number, y: number) => void,
) {
  const el = chart.container;
  let startX = 0,
    startY = 0,
    distance = 0,
    center = 0,
    initial: Range = { min: 0, max: 0 };
  let kind: "pending" | "pan" | "pinch" | "inspect" | "vertical" | null = null;
  let tapEligible = false;
  let hold: ReturnType<typeof setTimeout> | undefined,
    frame = 0,
    pending: Range | undefined;
  const position = (x: number) =>
    x - el.getBoundingClientRect().left - chart.plotLeft;
  const apply = (range: Range) => {
    pending = range;
    if (!frame)
      frame = requestAnimationFrame(() => {
        frame = 0;
        if (pending) changeRange(pending);
        pending = undefined;
      });
  };
  const inspect = (x: number, y: number) => {
    const e = chart.pointer.normalize({
      pageX: x + window.scrollX,
      pageY: y + window.scrollY,
    } as MouseEvent);
    const timestamp = chart.xAxis[0].toValue(e.chartX);
    const candidates = chart.series.filter(s => s.visible && ["temperature", "outdoor"].includes(s.options.id ?? ""));
    const points = candidates.flatMap(s => s.points).filter(p => p.y != null);
    // searchPoint builds its kd-tree asynchronously on the first call. A long press
    // must work on the first touch as well, so find the closest rendered sample.
    if (
      !points.length ||
      !points.some(p => Math.abs(chart.xAxis[0].toPixels(p.x) - e.chartX) <= 40)
    ) {
      chart.tooltip.hide();
      return;
    }
    const score = (p: Highcharts.Point) => Math.abs(chart.xAxis[0].toPixels(p.x) - e.chartX) * 3 + Math.abs(p.series.yAxis.toPixels(p.y!) - e.chartY);
    const point = points.filter(p => Math.abs(chart.xAxis[0].toPixels(p.x) - e.chartX) <= 40).reduce((best, p) => score(p) < score(best) ? p : best);
    if (point && point.y != null) {
      chart.tooltip.refresh(point);
      chart.xAxis[0].drawCrosshair(e, point);
    } else chart.tooltip.hide();
  };
  const onStart = (e: TouchEvent) => {
    e.stopImmediatePropagation();
    clearTimeout(hold);
    initial = current();
    tapEligible = e.touches.length === 1;
    if (e.touches.length >= 2) {
      kind = "pinch";
      distance = Math.max(
        10,
        Math.abs(e.touches[1].clientX - e.touches[0].clientX),
      );
      center = position((e.touches[0].clientX + e.touches[1].clientX) / 2);
    } else {
      startX = e.touches[0].clientX;
      startY = e.touches[0].clientY;
      kind = "pending";
      hold = setTimeout(() => {
        kind = "inspect";
        inspect(startX, startY);
      }, 450);
    }
  };
  const onMove = (e: TouchEvent) => {
    e.stopImmediatePropagation();
    if (e.touches.length >= 2 && kind === "pinch") {
      e.preventDefault();
      const width =
        ((initial.max - initial.min) * distance) /
        Math.max(10, Math.abs(e.touches[1].clientX - e.touches[0].clientX));
      const anchor =
        initial.min + (center / chart.plotWidth) * (initial.max - initial.min);
      const newCenter = position(
        (e.touches[0].clientX + e.touches[1].clientX) / 2,
      );
      const min = anchor - (newCenter / chart.plotWidth) * width;
      apply({ min, max: min + width });
      return;
    }
    if (e.touches.length !== 1) return;
    const { clientX: x, clientY: y } = e.touches[0],
      dx = x - startX,
      dy = y - startY;
    if (kind === "pending" && (Math.abs(dx) > 7 || Math.abs(dy) > 7)) {
      tapEligible = false;
      clearTimeout(hold);
      kind = Math.abs(dx) > Math.abs(dy) ? "pan" : "vertical";
    }
    if (kind === "pan") {
      e.preventDefault();
      const delta = (-dx / chart.plotWidth) * (initial.max - initial.min);
      apply({ min: initial.min + delta, max: initial.max + delta });
    }
    if (kind === "inspect") {
      e.preventDefault();
      inspect(x, y);
    }
  };
  const onEnd = (e: TouchEvent) => {
    e.stopImmediatePropagation();
    clearTimeout(hold);
    if (tapEligible && kind === "pending" && e.type !== "touchcancel" && e.touches.length === 0)
      onTap?.(position(startX) + chart.plotLeft, startY - el.getBoundingClientRect().top);
    if (e.touches.length === 1) {
      tapEligible = false;
      initial = current();
      startX = e.touches[0].clientX;
      startY = e.touches[0].clientY;
      kind = "pending";
    } else {
      kind = null;
      chart.tooltip.hide(700);
      chart.xAxis[0].hideCrosshair();
    }
  };
  const onWheel = (e: WheelEvent) => {
    if (e.ctrlKey || e.metaKey) {
      e.preventDefault();
      e.stopImmediatePropagation();
      const r = current(),
        fraction = Math.max(
          0,
          Math.min(1, position(e.clientX) / chart.plotWidth),
        ),
        width = (r.max - r.min) * Math.exp(e.deltaY * 0.008);
      const anchor = r.min + fraction * (r.max - r.min);
      apply({
        min: anchor - fraction * width,
        max: anchor + (1 - fraction) * width,
      });
    } else if (Math.abs(e.deltaX) > Math.abs(e.deltaY) || e.shiftKey) {
      e.preventDefault();
      const r = current(),
        delta =
          ((e.shiftKey ? e.deltaY : e.deltaX) / chart.plotWidth) *
          (r.max - r.min);
      apply({ min: r.min + delta, max: r.max + delta });
    }
  };
  el.addEventListener("touchstart", onStart, { capture: true, passive: true });
  el.addEventListener("touchmove", onMove, { capture: true, passive: false });
  el.addEventListener("touchend", onEnd, { capture: true });
  el.addEventListener("touchcancel", onEnd, { capture: true });
  el.addEventListener("wheel", onWheel, { capture: true, passive: false });
  return () => {
    clearTimeout(hold);
    cancelAnimationFrame(frame);
    el.removeEventListener("touchstart", onStart, true);
    el.removeEventListener("touchmove", onMove, true);
    el.removeEventListener("touchend", onEnd, true);
    el.removeEventListener("touchcancel", onEnd, true);
    el.removeEventListener("wheel", onWheel, true);
  };
}

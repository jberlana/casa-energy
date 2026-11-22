# Web application

React + TypeScript + Vite + Highcharts Stock. The application displays history collected by the local installation.

```sh
npm ci --prefix web
npm run dev --prefix web
npm run build --prefix web
```

Run from the repository root. The development proxy points to the local Python API on port 8767 unless `TADO_WEB_PORT` is set.
`dist/` and `node_modules/` are generated and must not be committed.

- `src/main.tsx`: app composition, selected room and shared range summary.
- `src/features/RefreshMenu.tsx`: explicit provider refresh actions, accessible keyboard navigation and bounded-job status.
- `src/features/heating/`: chart lifecycle, touch gestures, sessions and invoice context.
  `Windows.tsx` adds small ventilation markers and an on-demand detail; it does not remount the chart.
- `src/features/environment/`: exterior/weather data hook and weather presentation.
- `src/lib/`: common API types, time formatting, fetching and refresh invalidation.
- `src/style.css`: shared styles; split by feature only when that improves ownership.
- `public/`: reviewed static assets only. Never copy household screenshots or documents here.

Features may consume `lib`; keep shared modules independent of feature components. Do not introduce
cross-feature imports except explicit composition points (the timeline currently hosts weather).
Keep sources, units and confidence visible where they affect interpretation. No provider tokens or
account calls belong in the browser.

Gestures must keep horizontal pan, pinch anchor, long press and vertical page scroll independent.
Avoid remounting the chart during refresh. Preserve historical viewports; only a view pinned to now
follows the present. Test layout and gestures in-browser when changing those behaviors.

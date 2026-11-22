# Web application

React + TypeScript + Vite + Highcharts Stock. The public build starts with synthetic demo data.

```sh
npm ci --prefix web
npm run dev --prefix web
npm run build --prefix web
```

Run from the repository root. The development proxy points to the local Python API on port 8765.
`dist/` and `node_modules/` are generated and must not be committed.

- `src/main.tsx`: app composition, selected source/room and shared range summary.
- `src/features/heating/`: chart lifecycle, touch gestures, sessions and invoice context.
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

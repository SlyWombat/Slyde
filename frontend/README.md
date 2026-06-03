# memento-frontend

Modern web UI (React + TypeScript + Vite + Tailwind) for the Memento Manager. Talks only to the
backend REST API under `/api`.

```bash
npm install
npm run dev        # http://localhost:5173, proxies /api -> http://localhost:8080
npm run build      # outputs static assets to dist/ (served by the backend in production)
npm run typecheck  # tsc --noEmit
npm run lint
```

Set `VITE_API_TARGET` to point the dev proxy at a non-default backend URL.

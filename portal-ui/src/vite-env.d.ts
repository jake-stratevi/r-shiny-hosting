/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** "1" when running under `npm run dev:mock` (see .env.mock). */
  readonly VITE_PORTAL_MOCK?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

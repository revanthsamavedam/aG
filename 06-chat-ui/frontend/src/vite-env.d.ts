/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** URL of the AG-UI agent server. Default: http://localhost:8000 */
  readonly VITE_AGENT_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

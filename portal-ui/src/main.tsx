import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import { initializeAppearance } from './lib/appearance'
import './index.css'

// Self-hosted, bundled with the app. @fontsource ships one stylesheet per
// weight; we take the latin subset at 400/500/600 — the three the type scale
// actually uses — so nothing here reaches out to fonts.googleapis.com and the
// proxy's CSP does not have to be widened for a webfont.
import '@fontsource/instrument-sans/latin-400.css'
import '@fontsource/instrument-sans/latin-500.css'
import '@fontsource/instrument-sans/latin-600.css'

const root = document.getElementById('root')
if (!root) throw new Error('#root is missing from index.html')

// index.html has already applied the stored theme (before first paint); this
// re-applies it and starts tracking the OS while the choice is "system".
initializeAppearance()

createRoot(root).render(
  <StrictMode>
    {/* The proxy serves index.html for every non-API path on portal hosts, so
        history-API routing works without a hash. */}
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
)

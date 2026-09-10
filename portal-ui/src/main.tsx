import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import './index.css'

const root = document.getElementById('root')
if (!root) throw new Error('#root is missing from index.html')

createRoot(root).render(
  <StrictMode>
    {/* The proxy serves index.html for every non-API path on portal hosts, so
        history-API routing works without a hash. */}
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
)

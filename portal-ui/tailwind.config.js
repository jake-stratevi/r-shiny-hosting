/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      // Palette lifted from the proxy's operational pages (proxy_app/page.html)
      // so the SPA and the cold-start / error pages look like one product.
      // One accent (a sober blue) on a slate-grey ink scale; every other hue
      // in the UI is a status tint, never decoration.
      colors: {
        canvas: '#f7f8fa',
        surface: '#ffffff',
        line: '#e2e6ee',
        'line-soft': '#eef1f6',
        ink: '#1c2230',
        muted: '#55607a',
        faint: '#8a93a8',
        accent: {
          DEFAULT: '#3b6ce4',
          hover: '#2f5bc8',
          soft: '#eef3fe',
          line: '#c7d7fb',
        },
      },
      fontFamily: {
        // Bundled-only: system stack, no webfont request. The CSP the proxy
        // will eventually serve is unknown, so nothing here may reach out.
        sans: [
          '-apple-system',
          'BlinkMacSystemFont',
          'Segoe UI',
          'Roboto',
          'Helvetica',
          'Arial',
          'sans-serif',
        ],
        mono: [
          'ui-monospace',
          'SFMono-Regular',
          'Menlo',
          'Consolas',
          'monospace',
        ],
      },
      boxShadow: {
        card: '0 1px 2px rgba(20,30,60,.05)',
        'card-hover': '0 6px 20px -6px rgba(20,30,60,.16)',
        bar: '0 -1px 0 rgba(226,230,238,1), 0 -8px 20px -12px rgba(20,30,60,.18)',
      },
      borderRadius: {
        card: '12px',
        tile: '8px',
      },
      // Two card widths and a rail; nothing else needs a named size.
      gridTemplateColumns: {
        detail: 'minmax(0,2fr) minmax(18rem,1fr)',
        field: '13rem minmax(0,1fr)',
      },
    },
  },
  plugins: [],
}

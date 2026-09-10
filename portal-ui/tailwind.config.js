/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      // Palette lifted from the proxy's operational pages (proxy_app/page.html)
      // so the SPA and the cold-start / error pages look like one product.
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
        },
      },
      fontFamily: {
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
        card: '0 1px 3px rgba(20,30,60,.06)',
        'card-hover': '0 4px 14px rgba(20,30,60,.10)',
      },
      borderRadius: {
        card: '10px',
      },
    },
  },
  plugins: [],
}

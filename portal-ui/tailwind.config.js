/** @type {import('tailwindcss').Config} */

// Every colour is a semantic token defined as HSL channels in src/index.css,
// so a component says `bg-card` / `text-muted-foreground` / `border-border`
// and gets the right value in both themes without a `dark:` variant. The
// wrapper keeps `<alpha-value>` so `bg-accent/40` still works.
const token = (name) => `hsl(var(--${name}) / <alpha-value>)`

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  // Theme is a class on <html>, toggled by src/lib/appearance.ts.
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        background: token('background'),
        foreground: token('foreground'),
        card: {
          DEFAULT: token('card'),
          foreground: token('card-foreground'),
        },
        popover: {
          DEFAULT: token('popover'),
          foreground: token('popover-foreground'),
        },
        primary: {
          DEFAULT: token('primary'),
          foreground: token('primary-foreground'),
        },
        secondary: {
          DEFAULT: token('secondary'),
          foreground: token('secondary-foreground'),
        },
        muted: {
          DEFAULT: token('muted'),
          foreground: token('muted-foreground'),
        },
        accent: {
          DEFAULT: token('accent'),
          foreground: token('accent-foreground'),
        },
        destructive: {
          DEFAULT: token('destructive'),
          foreground: token('destructive-foreground'),
        },
        border: token('border'),
        input: token('input'),
        ring: token('ring'),
        // The one accent, by its role rather than by its plumbing: azure is
        // the focus ring AND the wayfinding colour (links, active trail,
        // "in progress" state). Same variable as --ring on purpose — one blue
        // in the product, never two that nearly match.
        azure: token('ring'),
        chart: {
          1: token('chart-1'),
          2: token('chart-2'),
          3: token('chart-3'),
          4: token('chart-4'),
          5: token('chart-5'),
        },
        sidebar: {
          DEFAULT: token('sidebar-background'),
          foreground: token('sidebar-foreground'),
          primary: token('sidebar-primary'),
          'primary-foreground': token('sidebar-primary-foreground'),
          accent: token('sidebar-accent'),
          'accent-foreground': token('sidebar-accent-foreground'),
          border: token('sidebar-border'),
          ring: token('sidebar-ring'),
        },
      },
      borderRadius: {
        xl: 'calc(var(--radius) + 4px)',
        lg: 'var(--radius)',
        md: 'calc(var(--radius) - 2px)',
        sm: 'calc(var(--radius) - 4px)',
      },
      fontFamily: {
        // Self-hosted via @fontsource/instrument-sans (weights 400/500/600,
        // latin subset), imported in main.tsx. Nothing here reaches out to
        // Google — the CSP the proxy serves is not ours to widen.
        sans: [
          'Instrument Sans',
          'ui-sans-serif',
          'system-ui',
          'sans-serif',
          'Apple Color Emoji',
          'Segoe UI Emoji',
          'Segoe UI Symbol',
          'Noto Color Emoji',
        ],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Consolas', 'monospace'],
      },
      spacing: {
        sidebar: 'var(--sidebar-width)',
        'sidebar-icon': 'var(--sidebar-width-icon)',
      },
      // Two card widths and a rail; nothing else needs a named size.
      gridTemplateColumns: {
        detail: 'minmax(0,2fr) minmax(18rem,1fr)',
        field: '13rem minmax(0,1fr)',
      },
      keyframes: {
        'slide-in-left': {
          from: { transform: 'translateX(-100%)' },
          to: { transform: 'translateX(0)' },
        },
      },
      animation: {
        'slide-in-left': 'slide-in-left 200ms ease-out',
      },
    },
  },
  plugins: [],
}

/** @type {import('tailwindcss').Config} */
export default {
  content: [
    './index.html',
    './src/**/*.{js,ts,jsx,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        page: '#fafaf9',
        surface: '#f5f5f4',
        'surface-raised': '#e7e5e4',
        line: '#d6d3d1',
        ink: '#1c1917',
        'ink-secondary': 'rgba(28, 25, 23, .7)',
        'ink-tertiary': 'rgba(28, 25, 23, .48)',
        accent: '#c8102e',
        'accent-soft': 'rgba(200, 16, 46, .1)',
        'accent-deep': '#8b0a1e',
        card: '#ffffff',
        'card-foreground': '#1c1917',
      },
      fontFamily: {
        sans: ['"IBM Plex Sans"', '"Helvetica Neue"', 'Arial', 'system-ui', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      borderRadius: {
        none: '0',
        sm: '4px',
        md: '8px',
        lg: '12px',
        xl: '16px',
      },
      spacing: {
        128: '32rem',
      },
    },
  },
  plugins: [],
};

/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: { extend: { colors: { ink: '#122b3b', muted: '#68808b', teal: '#168a86', canvas: '#f4f7f7', line: '#e5ecec' }, boxShadow: { panel: '0 10px 35px rgba(19, 49, 63, .06)' } } },
  plugins: [],
}

/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        brand: {
          blue: '#022397',
          light: '#00aeef',
          yellow: '#dfbd25',
          red: '#d2232a',
          green: '#41ad49',
          orange: '#e36837',
        },
      },
      fontFamily: {
        headline: ['Montserrat', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        body: ['Montserrat', 'ui-sans-serif', 'system-ui', 'sans-serif'],
      },
    },
  },
  plugins: [],
}

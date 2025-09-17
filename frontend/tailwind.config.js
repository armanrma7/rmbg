/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      boxShadow: {
        card: '0 10px 25px -10px rgba(0,0,0,0.2)'
      }
    }
  },
  plugins: [],
}


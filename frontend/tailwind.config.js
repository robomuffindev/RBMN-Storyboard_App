/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        'section-intro': '#3b82f6',
        'section-verse': '#10b981',
        'section-chorus': '#f97316',
        'section-bridge': '#a855f7',
        'section-outro': '#ef4444',
      },
    },
  },
  plugins: [],
  darkMode: 'class',
}

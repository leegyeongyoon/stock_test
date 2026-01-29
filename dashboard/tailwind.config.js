/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './app/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        // 커스텀 색상
        profit: '#22c55e',
        loss: '#ef4444',
        warning: '#f59e0b',
      },
    },
  },
  plugins: [],
}

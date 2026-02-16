/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './templates/**/*.html',
    './library/templates/**/*.html',
    './romcollections/templates/**/*.html',
    './devices/templates/**/*.html',
    './static/js/**/*.js',
  ],
  theme: {
    extend: {
      fontFamily: {
        'heading': ['VT323', 'monospace'],
        'body': ['Public Sans', 'sans-serif'],
      },
    },
  },
  plugins: [],
}

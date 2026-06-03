/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#0b0e14",
        panel: "#141925",
        edge: "#222a3a",
        accent: "#5b8cff",
      },
    },
  },
  plugins: [],
};

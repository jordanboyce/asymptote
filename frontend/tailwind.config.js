/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{vue,js,ts,jsx,tsx}",
  ],
  plugins: [
    require('daisyui'),
  ],
  // daisyUI v5+ reads its theme list from the @plugin block in style.css
  // (themes: corporate --default, business --prefersdark). This object is
  // kept for any tooling that still inspects tailwind.config.js, and the
  // values are intentionally kept in sync with style.css.
  daisyui: {
    themes: ["corporate", "business"],
  },
}

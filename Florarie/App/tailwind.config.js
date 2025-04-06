 /** @type {import('tailwindcss').Config} */
 module.exports = {
  content: ["./templates/**/*.html", "./**/templates/**/*.html"],
  darkMode: "media",
  daisyui: {
    themes: ["light", "dark", "cupcake"], // Add themes here
  },
  plugins: [require('@tailwindcss/forms'),require("daisyui")], // DaisyUI plugin
};



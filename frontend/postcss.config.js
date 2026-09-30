// Tailwind CSS v4 uses a dedicated PostCSS plugin that also handles imports and
// vendor-prefixing, so the standalone `tailwindcss` and `autoprefixer` plugins
// from the v3 setup are no longer listed here.
module.exports = {
  plugins: {
    "@tailwindcss/postcss": {},
  },
};

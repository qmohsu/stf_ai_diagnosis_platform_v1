/* eslint-disable @typescript-eslint/no-require-imports -- this preload wrapper must execute before Next initializes ESM workers */
const path = require("node:path");
const compat = path.resolve(__dirname, "readlink-compat.cjs").replaceAll("\\", "/");
if (process.platform === "win32") {
  const preload = `--require=${compat}`;
  process.env.NODE_OPTIONS = [process.env.NODE_OPTIONS, preload].filter(Boolean).join(" ");
  require("./readlink-compat.cjs");
}

process.argv = [process.argv[0], require.resolve("next/dist/bin/next"), "build"];
require("next/dist/bin/next");

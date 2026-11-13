import { build } from "esbuild";
import { fileURLToPath } from "node:url";

await build({
  entryPoints: [fileURLToPath(new URL("./frontend/app.js", import.meta.url))],
  outfile: fileURLToPath(new URL("./frontend/assets/app.bundle.js", import.meta.url)),
  bundle: true,
  format: "esm",
  target: "es2022",
  minify: true,
  legalComments: "eof",
  logLevel: "info",
});

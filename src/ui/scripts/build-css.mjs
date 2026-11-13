// Builds planner_ui/static/app.css: the shared brand layer and the application shell are
// concatenated (Tailwind takes one input; the @tailwind directives lead), then compiled and
// minified. The output is a build artifact and not committed.
import { spawnSync } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const watch = process.argv.includes("--watch");
const build = join(root, ".build");
mkdirSync(build, { recursive: true });
const input = join(build, "tailwind.input.css");
writeFileSync(
  input,
  readFileSync(join(root, "tailwind.brand.css"), "utf8") +
    "\n" +
    readFileSync(join(root, "tailwind.app.css"), "utf8"),
);
const output = join(root, "planner_ui", "static", "app.css");
mkdirSync(dirname(output), { recursive: true });

const args = [
  "tailwindcss",
  "-c", "tailwind.config.js",
  "--content", "planner_ui/templates/**/*.html,planner_ui/static/js/*.js",
  "-i", input,
  "-o", output,
  watch ? "--watch" : "--minify",
];
const result = spawnSync("npx", ["--no-install", ...args], {
  cwd: root,
  stdio: "inherit",
  shell: process.platform === "win32",
});
process.exit(result.status ?? 1);

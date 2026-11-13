// Copies the third-party browser files out of node_modules into the package, so the interface
// runs without a CDN and without Node at run time. The copies are committed; run
// `npm run vendor` after changing a version in package.json.
import { copyFileSync, mkdirSync, existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const modules = join(root, "node_modules");
const target = join(root, "planner_ui", "static");

// Tabler outline icons used by the interface (the names are the file names).
const ICONS = [
  "alert-triangle", "arrow-right", "arrows-sort", "ban", "calendar", "calendar-off", "certificate",
  "check", "chevron-down", "chevron-left", "chevron-right", "circle-check", "circle-x",
  "clipboard-list", "dots-vertical", "download", "edit", "history", "info-circle", "key",
  "language", "layout-dashboard", "layout-kanban", "layout-sidebar-left-collapse",
  "layout-sidebar-left-expand", "list-check", "lock", "logout", "menu-2", "moon", "pencil",
  "player-play", "plug-connected", "plus", "refresh", "search", "settings", "shield-lock", "sun",
  "trash", "upload", "user", "user-circle", "users", "users-group", "x",
];

const FILES = [
  ["htmx.org/dist/htmx.min.js", "vendor/htmx.min.js"],
  ["@alpinejs/csp/dist/cdn.min.js", "vendor/alpine-csp.min.js"],
  ["sortablejs/Sortable.min.js", "vendor/sortable.min.js"],
  ["htmx.org/LICENSE", "vendor/licenses/htmx.LICENSE"],
  ["sortablejs/LICENSE", "vendor/licenses/sortable.LICENSE"],
  ["@fontsource/manrope/LICENSE", "vendor/licenses/manrope.LICENSE"],
  ["@fontsource/ibm-plex-sans/LICENSE", "vendor/licenses/ibm-plex-sans.LICENSE"],
  ["@tabler/icons/LICENSE", "vendor/licenses/tabler-icons.LICENSE"],
  ...["500", "700", "800"].map((w) => [
    `@fontsource/manrope/files/manrope-latin-${w}-normal.woff2`,
    `fonts/files/manrope-latin-${w}-normal.woff2`,
  ]),
  ...["400", "500", "600"].map((w) => [
    `@fontsource/ibm-plex-sans/files/ibm-plex-sans-latin-${w}-normal.woff2`,
    `fonts/files/ibm-plex-sans-latin-${w}-normal.woff2`,
  ]),
  ...ICONS.map((name) => [`@tabler/icons/icons/outline/${name}.svg`, `../icons/${name}.svg`]),
];

let missing = 0;
for (const [from, to] of FILES) {
  const source = join(modules, from);
  const destination = join(target, to);
  if (!existsSync(source)) {
    console.error(`missing in node_modules: ${from}`);
    missing += 1;
    continue;
  }
  mkdirSync(dirname(destination), { recursive: true });
  copyFileSync(source, destination);
}
const version = (name) => JSON.parse(readFileSync(join(modules, name, "package.json"), "utf8")).version;
console.log(
  `htmx ${version("htmx.org")}, alpine ${version("@alpinejs/csp")}, sortable ${version("sortablejs")}`,
);
process.exit(missing ? 1 : 0);

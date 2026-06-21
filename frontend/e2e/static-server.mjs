import { createReadStream, existsSync, statSync } from "node:fs";
import { createServer } from "node:http";
import { extname, join, resolve, sep } from "node:path";

const args = new Map();
for (let index = 2; index < process.argv.length; index += 2) {
  args.set(process.argv[index], process.argv[index + 1]);
}

const host = args.get("--host") || "127.0.0.1";
const port = Number(args.get("--port") || process.env.PLAYWRIGHT_PORT || "4173");
const root = resolve(args.get("--root") || "dist");
const indexFile = join(root, "index.html");

const contentTypes = new Map([
  [".html", "text/html; charset=utf-8"],
  [".js", "text/javascript; charset=utf-8"],
  [".css", "text/css; charset=utf-8"],
  [".json", "application/json; charset=utf-8"],
  [".svg", "image/svg+xml"],
  [".png", "image/png"],
  [".jpg", "image/jpeg"],
  [".jpeg", "image/jpeg"],
  [".ico", "image/x-icon"]
]);

const server = createServer((request, response) => {
  const target = resolvePath(request.url || "/");
  if (!target) {
    response.writeHead(403);
    response.end("forbidden");
    return;
  }
  const file = existsSync(target) && statSync(target).isFile() ? target : indexFile;
  if (!existsSync(file)) {
    response.writeHead(404);
    response.end("not found");
    return;
  }
  response.writeHead(200, {
    "Content-Type": contentTypes.get(extname(file)) || "application/octet-stream",
    "Cache-Control": "no-store"
  });
  createReadStream(file).pipe(response);
});

server.listen(port, host, () => {
  console.log(`static server listening on http://${host}:${port}`);
});

function resolvePath(rawUrl) {
  const url = new URL(rawUrl, `http://${host}:${port}`);
  const pathname = decodeURIComponent(url.pathname);
  const file = resolve(join(root, pathname === "/" ? "index.html" : pathname));
  if (file !== root && !file.startsWith(`${root}${sep}`)) {
    return null;
  }
  return file;
}

// Standard Swagger UI for /api/v2/openapi.json — a static HTML page, no npm dependency.
// swagger-ui-dist is pinned to an exact cdnjs version with SRI (sha512 verified against the served bytes);
// bumping the version means recomputing all three hashes.

export const dynamic = "force-static";

const VERSION = "5.17.14";
const CDN = `https://cdnjs.cloudflare.com/ajax/libs/swagger-ui/${VERSION}`;
const SRI = {
  css: "sha512-+9UD8YSD9GF7FzOH38L9S6y56aYNx3R4dYbOCgvTJ2ZHpJScsahNdaMQJU/8osUiz9FPu0YZ8wdKf4evUbsGSg==",
  bundle: "sha512-7ihPQv5ibiTr0DW6onbl2MIKegdT6vjpPySyIb4Ftp68kER6Z7Yiub0tFoMmCHzZfQE9+M+KSjQndv6NhYxDgg==",
  preset: "sha512-UrYi+60Ci3WWWcoDXbMmzpoi1xpERbwjPGij6wTh8fXl81qNdioNNHExr9ttnBebKF0ZbVnPlTPlw+zECUK1Xw==",
};

// The bar mirrors the site's light tokens (globals.css) because this page does not load the app stylesheet.
const html = `<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Swagger UI — 판결 아카이브 API v2</title>
  <meta name="description" content="판결 아카이브 REST API v2 OpenAPI 3.1 명세의 Swagger UI">
  <link rel="stylesheet" href="${CDN}/swagger-ui.min.css" integrity="${SRI.css}" crossorigin="anonymous" referrerpolicy="no-referrer">
  <style>
    :root { --background: hsl(43 36% 95%); --foreground: hsl(28 18% 12%); --muted: hsl(30 10% 38%); --stamp: hsl(354 70% 42%); }
    body { margin: 0; background: #fff; }
    .va-bar { display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: center; justify-content: space-between;
      padding: 10px 16px; background: var(--background); color: var(--foreground);
      border-bottom: 4px double color-mix(in srgb, var(--foreground) 70%, transparent);
      font: 700 12px/1.4 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; letter-spacing: .08em; }
    .va-bar a { color: inherit; text-decoration: none; min-height: 32px; display: inline-flex; align-items: center; }
    .va-bar a:hover { text-decoration: underline; text-decoration-color: var(--stamp); text-decoration-thickness: 2px; }
    .va-bar a:focus-visible { outline: 2px solid var(--stamp); outline-offset: 2px; }
    .va-bar span { color: var(--muted); font-weight: 600; }
    .swagger-ui .info { margin: 24px 0; }
  </style>
</head>
<body>
  <header class="va-bar">
    <a href="/developers">← API 레퍼런스</a>
    <span>Swagger UI ${VERSION} · <a href="/api/v2/openapi.json">/api/v2/openapi.json</a></span>
  </header>
  <main id="swagger-ui"></main>
  <script src="${CDN}/swagger-ui-bundle.min.js" integrity="${SRI.bundle}" crossorigin="anonymous" referrerpolicy="no-referrer"></script>
  <script src="${CDN}/swagger-ui-standalone-preset.min.js" integrity="${SRI.preset}" crossorigin="anonymous" referrerpolicy="no-referrer"></script>
  <script>
    window.addEventListener("load", function () {
      window.ui = SwaggerUIBundle({
        url: "/api/v2/openapi.json",
        dom_id: "#swagger-ui",
        deepLinking: true,
        tryItOutEnabled: true,
        displayRequestDuration: true,
        defaultModelsExpandDepth: 0,
        presets: [SwaggerUIBundle.presets.apis, SwaggerUIStandalonePreset],
        plugins: [SwaggerUIBundle.plugins.DownloadUrl],
        layout: "BaseLayout",
      });
    });
  </script>
</body>
</html>`;

export function GET() {
  return new Response(html, {
    headers: {
      "Content-Type": "text/html; charset=utf-8",
      "Cache-Control": "public, max-age=300, stale-while-revalidate=86400",
    },
  });
}

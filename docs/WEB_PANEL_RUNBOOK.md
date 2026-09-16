# Web Panel Runbook — دستیار هوشمند کسب‌وکارهای مجازی

Status: Phase 9 — Operational
Date: 2026-09-16

Per TECHNOLOGY_AND_REPO_SPECIFICATION_V1 section 4 and IMPLEMENTATION_ROADMAP_V1 Phase 9.

## 1. Architecture

- Server-rendered FastAPI/Jinja2 pages
- HTMX for interactive actions/partial updates
- Small JavaScript layer only where needed (static/js/app.js)
- No Node runtime, low resource footprint (suitable for weak VPS)
- Unified permission/use-case layer: same application services as JSON API
- Authorization server-side: session cookie identifies user, business scope resolved per request, cross-tenant fails closed (404)

## 2. Routes

- `/web/login` — login page (GET) + submit (POST)
- `/web/register` — register page (GET) + submit (POST)
- `/web/logout` — logout (revokes session, clears cookie)
- `/web/` — dashboard (lists user businesses, quick links)
- `/web/businesses` — list businesses + create form
- `/web/businesses/{id}` — business detail (tabs, stats, sync jobs, notifications)
- `/web/businesses/{id}/products` — product list with search/state filter
- `/web/businesses/{id}/products/{product_id}` — product detail (meta, attributes, preview, versions, publications)
- `/web/businesses/{id}/sync-jobs/partial` — HTMX partial for sync jobs (auto-refresh every 30s)
- `/web/businesses/{id}/notifications/partial` — HTMX partial for notifications (every 60s)
- `/web/businesses/{id}/publications/partial` — HTMX partial for publications

Future (Phase 9 extension):
- `/web/businesses/{id}/sources` — sources list + create
- `/web/businesses/{id}/connections` — connections list + create/verify
- `/web/businesses/{id}/billing` — subscription status, credits
- `/web/businesses/{id}/members` — members, invites, requests

## 3. Templates

Location: `templates/`

- `base.html` — layout, header/nav, footer, HTMX CDN, CSS/JS
- `login.html`, `register.html` — auth
- `dashboard.html` — welcome + businesses + quick links
- `businesses.html` — list + create
- `business_detail.html` — tabs, stats, HTMX partials
- `products.html` — table with search
- `product_detail.html` — detail + preview + versions + publications
- `error.html` — generic error

All templates RTL (dir="rtl", lang="fa"), Persian UI.

## 4. Static Assets

- `static/css/app.css` — styles (variables, layout, cards, tables, badges)
- `static/js/app.js` — flash auto-hide, HTMX error handling, confirm dialogs
- `static/certification_photo.jpg` — guaranteed-reachable test image for platform certification (Bale, Telegram)
- Served via nginx in prod (expires 7d), via FastAPI StaticFiles in dev

## 5. Security

- Session cookie: httponly, samesite=lax, 30d max_age
- CSRF: SameSite + no state-changing GET (POST for login/register)
- XSS: Jinja2 auto-escape, CSP header (default-src self, script-src self + unpkg.com for HTMX, style-src self unsafe-inline)
- No secrets in templates or logs
- Authorization checked in every web route via biz_svc.require_business_access

## 6. HTMX Usage

- `hx-get` for partials (sync jobs, notifications, publications)
- `hx-trigger="load, every 30s"` for auto-refresh
- `hx-target` for preview box
- `hx-boost="true"` for tabs (progressive enhancement)
- Error handling: htmx:responseError logged to console

## 7. Deployment

- Dockerfile copies templates/ and static/
- docker-compose mounts static via nginx volume
- Nginx serves /static/ directly (efficient)
- Version shown in footer from app.__version__

## 8. Future Improvements

- Add HTMX for product search (live search)
- Add pagination for products (currently all)
- Add modals for create source/connection
- Add real-time notifications via SSE or WebSocket
- Add dark mode
- Add PWA manifest for mobile

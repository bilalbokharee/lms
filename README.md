# From Bilal — Future-Proof Engineer LMS

A self-hostable Learning Management System with a curated, opinionated path from "what even is a computer" to "paid AI engineer." Built for serious learners who want a real roadmap, not 87 abandoned tutorial folders.

## What's inside

- **14 phases, 80+ lessons** — Computing basics → Python → Engineer's Toolkit (Git/terminal/IDEs/AI helpers) → Databases → Frontend → Backend → Cloud/DevOps → CS Fundamentals → Math → ML → Deep Learning & LLMs → Career launch
- **Curated paid + free resources** — Andrew Ng, Karpathy, Colt Steele, Jonas Schmedtmann, fast.ai, MIT, Hugging Face
- **Hands-on projects** for every lesson — building beats watching
- **Earning milestones** — clear markers of when you can start making money (freelance, internship, residency)
- **Gamified UI** — XP, ranks (Rookie → Master), streak counter, confetti on submission
- **User accounts** with progress tracking
- **Admin review queue** — submissions stay pending until you approve them
- **Trust signals** — passive time-on-page tracking and burst detection in the admin view
- **Single SQLite file** — fully portable, drop the DB anywhere

## How accountability works (the honest version)

Marking "complete" alone is too easy. The LMS uses these layers:

1. **Mandatory reflection.** Every lesson requires a free-text answer (min 50 chars): *what did you learn? what's still confusing?* You read these.
2. **Optional project link** for project lessons (GitHub repo, deployed URL, screenshot, doc) — encouraged but not blocking, because beginners don't have GitHub on day one.
3. **Admin review queue.** Submissions land in `pending-review`. **They don't count toward XP/progress until you approve them.** Open `/admin/review` to see each submission with reflection inline and click-through to any project link.
4. **Passive trust signals** on `/admin/user/<id>`: time-on-page ratio (heartbeat tracked while the tab is visible), lessons-in-last-24h burst count, full submission history.

What this catches: rapid clicking, fake completions, copy-paste reflections you can spot reading them.

What it cannot catch: someone who reads slowly and writes a real-sounding reflection. The unfakeable layer is human — sit with the learner once a week and ask three random questions about completed lessons.

## Run it locally

You need Python 3.10+.

```bash
cd LMS
pip install -r requirements.txt
SUPERUSER_PASSWORD='your-strong-password' python app.py
```

Open http://localhost:5000.

If port 5000 is busy (macOS AirPlay…), use a different port:

```bash
PORT=5500 python app.py
```

A default superuser is auto-created on first run:

- **Username:** `admin`
- **Password:** `changeme123` (override via `SUPERUSER_PASSWORD` env var)

## Ship it (recommended: Fly.io)

Fly.io is the best free option because it has persistent volumes — your SQLite database survives restarts. Render's free tier does NOT, you'd lose data on every redeploy unless you pay for a disk.

### Fly.io (free tier, recommended)

1. Push this folder to a GitHub repo
2. Install the Fly CLI: https://fly.io/docs/hands-on/install-flyctl/
3. Sign up & log in: `fly auth signup` then `fly auth login`
4. From the LMS folder:

```bash
fly launch --no-deploy --copy-config           # uses the included fly.toml; pick a unique app name
fly volumes create lms_data --size 1 --region fra   # pick your region (fra, iad, sin, bom...)
fly secrets set \
  SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')" \
  SUPERUSER_USERNAME=bilal \
  SUPERUSER_PASSWORD='your-strong-password'
fly deploy
```

Your URL will be `https://<your-app-name>.fly.dev`. Suggested app name: `frombilal`.

The free tier puts the machine to sleep when idle and wakes on request (~1–2s cold start). Fine for a small group.

### Render (paid plan needed for SQLite persistence)

The included `render.yaml` is a Render Blueprint. After pushing to GitHub:

1. https://render.com → New → Blueprint → connect repo
2. It'll detect `render.yaml`. Confirm.
3. **Set the Starter plan ($7/mo)** to get a persistent disk. The free plan loses your DB on redeploy.
4. In the dashboard set the secrets: `SECRET_KEY`, `SUPERUSER_USERNAME`, `SUPERUSER_PASSWORD`.

If you want to use Render's free tier, switch the DB to PostgreSQL (Render offers a free 90-day trial Postgres instance, then $7/mo). That's a code change — happy to add SQLAlchemy + Postgres support if you want.

### Other free options

- **PythonAnywhere** — has free tier with file persistence. Good for non-Docker Python apps. Use the included `Procfile`-style `gunicorn -w 2 -b 0.0.0.0:$PORT app:app` command.
- **Railway** — free trial credits, easy to deploy.
- **Your own machine via Cloudflare Tunnel** — totally free. `cloudflared tunnel --url http://localhost:5000` gives you a free public HTTPS URL pointing to your laptop.

## Customizing the curriculum

Edit `curriculum.json`. Phases have a `color` and `icon` field that drive the UI. Schema:

```
{
  "phases": [{
    "id": "phase-X",
    "title": "...",
    "color": "#8b5cf6",     // hex color for accent
    "icon": "🚀",            // emoji
    "duration_weeks": "...",
    "objective": "...",
    "outcome": "...",
    "modules": [{
      "title": "...",
      "lessons": [{
        "title": "...", "objective": "...", "hours": N,
        "reading": [{"name": "...", "url": "..."}],
        "courses": [{"platform": "Udemy", "title": "...", "url": "...", "highly_recommended": true}],
        "project": "..."
      }],
      "earning_milestone": "optional gold callout"
    }]
  }]
}
```

Restart the app after editing. Existing user progress survives if you only ADD lessons at the end of modules. Reordering shifts the keys.

## Files

- `app.py` — Flask backend
- `curriculum.json` — all course content (the part that took the most thought)
- `templates/` — Jinja2 HTML
- `static/style.css` — single dark vibrant stylesheet
- `requirements.txt` — Python deps
- `Dockerfile`, `fly.toml`, `render.yaml`, `Procfile`, `.dockerignore`, `.gitignore` — deployment

## Security notes

- Passwords hashed with Werkzeug `pbkdf2:sha256`
- HTTP-only, SameSite=Lax session cookies signed by `SECRET_KEY`
- Set `SECRET_KEY` to a stable value in production (env var) so sessions survive restarts
- Superuser self-deletion blocked
- Always serve over HTTPS in production (Fly/Render do this automatically)

## Future ideas (extension hooks)

- Email verification on signup, password reset
- Per-user notes from the admin
- Streak freeze (1 free skip day per week)
- Badge gallery (separate achievements beyond rank)
- Cohort feature with leaderboard
- Discord webhook on milestone hit

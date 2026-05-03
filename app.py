"""
Future-Proof Engineer LMS
A self-hostable Learning Management System with curated AI/Software-Engineering curriculum.

Run locally:
    pip install -r requirements.txt
    python app.py
    # open http://localhost:5000

Default superuser is created on first run if none exists:
    username: admin
    password: changeme123  (CHANGE THIS in .env or via the SUPERUSER_PASSWORD env var)
"""

import json
import os
import sqlite3
import secrets
from datetime import datetime
from functools import wraps
from pathlib import Path

from flask import (
    Flask, g, render_template, request, redirect,
    url_for, session, flash, jsonify, abort
)
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = Path(__file__).parent.resolve()
DB_PATH = Path(os.environ.get("DB_PATH", BASE_DIR / "lms.db"))
CURRICULUM_PATH = BASE_DIR / "curriculum.json"

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", secrets.token_hex(32))
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

# -------------------- Curriculum loading --------------------

with open(CURRICULUM_PATH, "r", encoding="utf-8") as f:
    CURRICULUM = json.load(f)


def lesson_key(phase_id: str, m_idx: int, l_idx: int) -> str:
    return f"{phase_id}::{m_idx}::{l_idx}"


def all_lesson_keys():
    keys = []
    for phase in CURRICULUM["phases"]:
        for m_idx, module in enumerate(phase.get("modules", [])):
            for l_idx, _ in enumerate(module.get("lessons", [])):
                keys.append(lesson_key(phase["id"], m_idx, l_idx))
    return keys


def find_phase(phase_id: str):
    for p in CURRICULUM["phases"]:
        if p["id"] == phase_id:
            return p
    return None


def find_lesson(phase_id: str, m_idx: int, l_idx: int):
    phase = find_phase(phase_id)
    if not phase:
        return None, None, None
    try:
        module = phase["modules"][m_idx]
        lesson = module["lessons"][l_idx]
        return phase, module, lesson
    except (IndexError, KeyError):
        return phase, None, None


# -------------------- Database --------------------

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            is_superuser INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS progress (
            user_id INTEGER NOT NULL,
            lesson_key TEXT NOT NULL,
            completed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, lesson_key),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS lesson_time (
            user_id INTEGER NOT NULL,
            lesson_key TEXT NOT NULL,
            seconds_active INTEGER NOT NULL DEFAULT 0,
            last_heartbeat_at TEXT,
            PRIMARY KEY (user_id, lesson_key),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        """
    )
    # Additive migrations — safe on existing DBs
    existing_cols = {r[1] for r in conn.execute("PRAGMA table_info(progress)").fetchall()}
    for col, ddl in [
        ("reflection",      "ALTER TABLE progress ADD COLUMN reflection TEXT"),
        ("submission_url",  "ALTER TABLE progress ADD COLUMN submission_url TEXT"),
        ("submission_notes","ALTER TABLE progress ADD COLUMN submission_notes TEXT"),
        ("status",          "ALTER TABLE progress ADD COLUMN status TEXT NOT NULL DEFAULT 'pending-review'"),
        ("reviewer_id",     "ALTER TABLE progress ADD COLUMN reviewer_id INTEGER"),
        ("reviewed_at",     "ALTER TABLE progress ADD COLUMN reviewed_at TEXT"),
        ("reviewer_notes",  "ALTER TABLE progress ADD COLUMN reviewer_notes TEXT"),
        ("seconds_at_complete","ALTER TABLE progress ADD COLUMN seconds_at_complete INTEGER NOT NULL DEFAULT 0"),
    ]:
        if col not in existing_cols:
            conn.execute(ddl)
    conn.commit()

    # Bootstrap a superuser if none exists
    cur = conn.execute("SELECT COUNT(*) FROM users WHERE is_superuser = 1")
    if cur.fetchone()[0] == 0:
        admin_user = os.environ.get("SUPERUSER_USERNAME", "admin")
        admin_email = os.environ.get("SUPERUSER_EMAIL", "admin@example.com")
        admin_password = os.environ.get("SUPERUSER_PASSWORD", "changeme123")
        try:
            conn.execute(
                "INSERT INTO users (username, email, password_hash, is_superuser) VALUES (?, ?, ?, 1)",
                (admin_user, admin_email, generate_password_hash(admin_password)),
            )
            conn.commit()
            print(f"[init_db] Superuser created: {admin_user} / {admin_password}")
            print("[init_db] CHANGE THIS PASSWORD via the SUPERUSER_PASSWORD env var or by re-creating the DB.")
        except sqlite3.IntegrityError:
            pass
    conn.close()


# -------------------- Auth helpers --------------------

def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    row = get_db().execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    return row


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def superuser_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        u = current_user()
        if not u or not u["is_superuser"]:
            abort(403)
        return view(*args, **kwargs)
    return wrapped


@app.context_processor
def inject_globals():
    return {"current_user": current_user(), "curriculum_meta": CURRICULUM["meta"]}


# -------------------- Progress queries --------------------

def get_user_progress(user_id: int) -> set:
    """Set of lesson keys the user has at least submitted (any status)."""
    rows = get_db().execute(
        "SELECT lesson_key FROM progress WHERE user_id = ?", (user_id,)
    ).fetchall()
    return {r["lesson_key"] for r in rows}


def get_user_progress_by_status(user_id: int) -> dict:
    """Map lesson_key -> status for a user."""
    rows = get_db().execute(
        "SELECT lesson_key, status FROM progress WHERE user_id = ?", (user_id,)
    ).fetchall()
    return {r["lesson_key"]: r["status"] for r in rows}


def get_user_approved(user_id: int) -> set:
    """Set of lesson keys the user has had APPROVED — these count for stats."""
    rows = get_db().execute(
        "SELECT lesson_key FROM progress WHERE user_id = ? AND status = 'approved'",
        (user_id,),
    ).fetchall()
    return {r["lesson_key"] for r in rows}


def get_lesson_seconds(user_id: int, lesson_key: str) -> int:
    row = get_db().execute(
        "SELECT seconds_active FROM lesson_time WHERE user_id = ? AND lesson_key = ?",
        (user_id, lesson_key),
    ).fetchone()
    return row["seconds_active"] if row else 0


def compute_rank(pct: int):
    """Pick the highest-tier rank whose min_pct <= pct."""
    ranks = CURRICULUM.get("ranks", [])
    if not ranks:
        return None
    chosen = ranks[0]
    for r in ranks:
        if pct >= r["min_pct"]:
            chosen = r
    # Look ahead to next rank for progress-toward-next
    next_rank = None
    for r in ranks:
        if r["min_pct"] > pct:
            next_rank = r
            break
    return {**chosen, "next": next_rank}


def compute_streak(user_id: int) -> int:
    """Consecutive days (counting today) with at least one APPROVED lesson.
    A user is on a streak if they've been approved for a lesson today AND every previous day going back.
    Gracefully handles timezone via SQLite's date('now', 'localtime').
    """
    db = get_db()
    rows = db.execute(
        """SELECT DISTINCT date(reviewed_at, 'localtime') AS d
           FROM progress WHERE user_id = ? AND status = 'approved' AND reviewed_at IS NOT NULL
           ORDER BY d DESC""",
        (user_id,),
    ).fetchall()
    if not rows:
        return 0
    days = [r["d"] for r in rows if r["d"]]
    if not days:
        return 0

    from datetime import date, timedelta
    today = date.today()
    streak = 0
    cursor = today
    day_set = set(days)
    # Allow today OR yesterday as starting point (so a streak is preserved if you haven't completed yet today)
    if str(today) not in day_set:
        cursor = today - timedelta(days=1)
        if str(cursor) not in day_set:
            return 0
    while str(cursor) in day_set:
        streak += 1
        cursor = cursor - timedelta(days=1)
    return streak


def lesson_requirements(lesson: dict) -> dict:
    """Soft expectations shown on the lesson page.
    - expected_seconds: a friendly target the learner sees (NOT a hard lock)
    - has_project: lesson defines a project (URL field is OFFERED but optional)
    - reflection always required, min 50 chars
    """
    hours = lesson.get("hours", 1) or 1
    expected_seconds = max(10 * 60, int(hours * 30 * 60))
    return {
        "expected_seconds": expected_seconds,
        "expected_minutes": expected_seconds // 60,
        "has_project": bool(lesson.get("project")),
        "reflection_min_chars": 50,
    }


def compute_phase_stats(approved_set):
    """Stats are based ONLY on admin-approved lessons (no self-marking shortcut)."""
    stats = {}
    total_done = 0
    total_all = 0
    for phase in CURRICULUM["phases"]:
        done = 0
        total = 0
        for m_idx, module in enumerate(phase.get("modules", [])):
            for l_idx, _ in enumerate(module.get("lessons", [])):
                total += 1
                if lesson_key(phase["id"], m_idx, l_idx) in approved_set:
                    done += 1
        stats[phase["id"]] = {
            "done": done,
            "total": total,
            "pct": round(100 * done / total) if total else 0,
        }
        total_done += done
        total_all += total
    overall_pct = round(100 * total_done / total_all) if total_all else 0
    return stats, total_done, total_all, overall_pct


# -------------------- Routes --------------------

@app.route("/")
def index():
    if current_user():
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if current_user():
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""

        errors = []
        if len(username) < 3:
            errors.append("Username must be at least 3 characters.")
        if "@" not in email or "." not in email:
            errors.append("Please enter a valid email.")
        if len(password) < 8:
            errors.append("Password must be at least 8 characters.")

        if not errors:
            try:
                db = get_db()
                db.execute(
                    "INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)",
                    (username, email, generate_password_hash(password)),
                )
                db.commit()
                user = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
                session["user_id"] = user["id"]
                flash("Welcome aboard! Let's go.", "success")
                return redirect(url_for("dashboard"))
            except sqlite3.IntegrityError:
                errors.append("Username or email already taken.")
        for e in errors:
            flash(e, "error")
    return render_template("signup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        user = get_db().execute(
            "SELECT * FROM users WHERE username = ? OR email = ?", (username, username.lower())
        ).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            nxt = request.args.get("next") or url_for("dashboard")
            return redirect(nxt)
        flash("Invalid username or password.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    user = current_user()
    approved = get_user_approved(user["id"])
    status_map = get_user_progress_by_status(user["id"])
    stats, total_done, total_all, overall_pct = compute_phase_stats(approved)
    pending_count = sum(1 for s in status_map.values() if s == "pending-review")
    rank = compute_rank(overall_pct)
    streak = compute_streak(user["id"])
    xp = total_done  # 1 lesson = 1 XP for now
    return render_template(
        "dashboard.html",
        phases=CURRICULUM["phases"],
        domains=CURRICULUM["future_proof_domains"],
        approved=approved,
        status_map=status_map,
        stats=stats,
        total_done=total_done,
        total_all=total_all,
        overall_pct=overall_pct,
        pending_count=pending_count,
        rank=rank,
        streak=streak,
        xp=xp,
        lesson_key_fn=lesson_key,
    )


@app.route("/phase/<phase_id>")
@login_required
def phase_view(phase_id):
    phase = find_phase(phase_id)
    if not phase:
        abort(404)
    user = current_user()
    approved = get_user_approved(user["id"])
    status_map = get_user_progress_by_status(user["id"])
    return render_template(
        "phase.html",
        phase=phase,
        approved=approved,
        status_map=status_map,
        lesson_key_fn=lesson_key,
    )


@app.route("/lesson/<phase_id>/<int:m_idx>/<int:l_idx>", methods=["GET", "POST"])
@login_required
def lesson_view(phase_id, m_idx, l_idx):
    phase, module, lesson = find_lesson(phase_id, m_idx, l_idx)
    if not (phase and module and lesson):
        abort(404)
    user = current_user()
    key = lesson_key(phase_id, m_idx, l_idx)
    db = get_db()
    reqs = lesson_requirements(lesson)

    if request.method == "POST":
        action = request.form.get("action")
        if action == "submit":
            reflection = (request.form.get("reflection") or "").strip()
            sub_url = (request.form.get("submission_url") or "").strip()
            sub_notes = (request.form.get("submission_notes") or "").strip()
            seconds = get_lesson_seconds(user["id"], key)

            errors = []
            if len(reflection) < reqs["reflection_min_chars"]:
                errors.append(
                    f"Reflection must be at least {reqs['reflection_min_chars']} characters. "
                    f"In your own words: what did you learn? what's still confusing?"
                )

            if errors:
                for e in errors:
                    flash(e, "error")
            else:
                db.execute(
                    """INSERT OR REPLACE INTO progress
                       (user_id, lesson_key, completed_at, reflection, submission_url,
                        submission_notes, status, seconds_at_complete)
                       VALUES (?, ?, CURRENT_TIMESTAMP, ?, ?, ?, 'pending-review', ?)""",
                    (user["id"], key, reflection, sub_url, sub_notes, seconds),
                )
                db.commit()
                flash("Submitted! Bilal bhai will review.", "success")
            return redirect(url_for("lesson_view", phase_id=phase_id, m_idx=m_idx, l_idx=l_idx))

        elif action == "withdraw":
            db.execute(
                "DELETE FROM progress WHERE user_id = ? AND lesson_key = ? AND status != 'approved'",
                (user["id"], key),
            )
            db.commit()
            flash("Submission withdrawn.", "info")
            return redirect(url_for("lesson_view", phase_id=phase_id, m_idx=m_idx, l_idx=l_idx))

    # GET: load existing submission (if any) and current time spent
    existing = db.execute(
        "SELECT * FROM progress WHERE user_id = ? AND lesson_key = ?",
        (user["id"], key),
    ).fetchone()
    seconds_so_far = get_lesson_seconds(user["id"], key)

    flat = []
    for p in CURRICULUM["phases"]:
        for mi, mod in enumerate(p.get("modules", [])):
            for li, _ in enumerate(mod.get("lessons", [])):
                flat.append((p["id"], mi, li))
    try:
        idx = flat.index((phase_id, m_idx, l_idx))
    except ValueError:
        idx = -1
    prev_link = flat[idx - 1] if idx > 0 else None
    next_link = flat[idx + 1] if 0 <= idx < len(flat) - 1 else None

    return render_template(
        "lesson.html",
        phase=phase,
        module=module,
        lesson=lesson,
        m_idx=m_idx,
        l_idx=l_idx,
        existing=existing,
        seconds_so_far=seconds_so_far,
        reqs=reqs,
        prev_link=prev_link,
        next_link=next_link,
        lesson_key=key,
    )


@app.route("/api/heartbeat", methods=["POST"])
@login_required
def heartbeat():
    """Lesson page pings this every ~30s while visible.
    Body: {"lesson_key": "...", "delta_seconds": 30}
    Server caps delta to 60s to prevent spoofing huge values.
    """
    user = current_user()
    data = request.get_json(silent=True) or {}
    key = data.get("lesson_key", "")
    delta = max(0, min(60, int(data.get("delta_seconds", 0) or 0)))
    if not key or delta == 0:
        return jsonify({"ok": False}), 400

    # Validate the key actually exists in curriculum (no fake keys)
    valid_keys = set(all_lesson_keys())
    if key not in valid_keys:
        return jsonify({"ok": False, "error": "invalid lesson"}), 400

    db = get_db()
    db.execute(
        """INSERT INTO lesson_time (user_id, lesson_key, seconds_active, last_heartbeat_at)
           VALUES (?, ?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(user_id, lesson_key) DO UPDATE SET
             seconds_active = seconds_active + excluded.seconds_active,
             last_heartbeat_at = excluded.last_heartbeat_at""",
        (user["id"], key, delta),
    )
    db.commit()
    seconds = get_lesson_seconds(user["id"], key)
    return jsonify({"ok": True, "seconds_active": seconds})


@app.route("/admin")
@login_required
@superuser_required
def admin_dashboard():
    db = get_db()
    users = db.execute(
        "SELECT id, username, email, is_superuser, created_at FROM users ORDER BY created_at DESC"
    ).fetchall()

    pending_total = db.execute(
        "SELECT COUNT(*) AS c FROM progress WHERE status = 'pending-review'"
    ).fetchone()["c"]

    user_rows = []
    for u in users:
        approved = get_user_approved(u["id"])
        _, done, total, pct = compute_phase_stats(approved)
        pending = db.execute(
            "SELECT COUNT(*) AS c FROM progress WHERE user_id = ? AND status = 'pending-review'",
            (u["id"],),
        ).fetchone()["c"]
        user_rows.append({
            "id": u["id"],
            "username": u["username"],
            "email": u["email"],
            "is_superuser": u["is_superuser"],
            "created_at": u["created_at"],
            "done": done,
            "total": total,
            "pct": pct,
            "pending": pending,
        })
    return render_template("admin.html", users=user_rows, pending_total=pending_total)


@app.route("/admin/review")
@login_required
@superuser_required
def admin_review_queue():
    db = get_db()
    rows = db.execute(
        """SELECT p.*, u.username, u.email
           FROM progress p
           JOIN users u ON u.id = p.user_id
           WHERE p.status = 'pending-review'
           ORDER BY p.completed_at ASC""",
    ).fetchall()

    queue = []
    for r in rows:
        try:
            phase_id, m_idx_s, l_idx_s = r["lesson_key"].split("::")
            m_idx, l_idx = int(m_idx_s), int(l_idx_s)
            phase, module, lesson = find_lesson(phase_id, m_idx, l_idx)
            if not (phase and module and lesson):
                continue
            queue.append({
                "user_id": r["user_id"],
                "username": r["username"],
                "lesson_key": r["lesson_key"],
                "phase_id": phase_id,
                "m_idx": m_idx,
                "l_idx": l_idx,
                "phase_title": phase["title"],
                "module_title": module["title"],
                "lesson_title": lesson["title"],
                "lesson_hours": lesson.get("hours", 0),
                "reflection": r["reflection"] or "",
                "submission_url": r["submission_url"] or "",
                "submission_notes": r["submission_notes"] or "",
                "seconds_at_complete": r["seconds_at_complete"] or 0,
                "completed_at": r["completed_at"],
                "has_project": bool(lesson.get("project")),
                "project_text": lesson.get("project", ""),
            })
        except (ValueError, KeyError):
            continue
    return render_template("admin_review.html", queue=queue)


@app.route("/admin/review/<int:user_id>/<path:lesson_key>", methods=["POST"])
@login_required
@superuser_required
def admin_review_decision(user_id, lesson_key):
    decision = request.form.get("decision")
    notes = (request.form.get("reviewer_notes") or "").strip()
    if decision not in ("approve", "reject"):
        abort(400)
    new_status = "approved" if decision == "approve" else "rejected"
    me = current_user()
    db = get_db()
    db.execute(
        """UPDATE progress
           SET status = ?, reviewer_id = ?, reviewed_at = CURRENT_TIMESTAMP, reviewer_notes = ?
           WHERE user_id = ? AND lesson_key = ?""",
        (new_status, me["id"], notes, user_id, lesson_key),
    )
    db.commit()
    flash(f"Lesson {decision}d.", "success" if decision == "approve" else "info")
    return redirect(request.referrer or url_for("admin_review_queue"))


@app.route("/admin/user/<int:user_id>")
@login_required
@superuser_required
def admin_user_detail(user_id):
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        abort(404)
    approved = get_user_approved(user_id)
    status_map = get_user_progress_by_status(user_id)
    stats, done, total, pct = compute_phase_stats(approved)
    completed_rows = db.execute(
        """SELECT p.lesson_key, p.completed_at, p.status, p.seconds_at_complete,
                  p.reflection, p.submission_url, p.submission_notes,
                  p.reviewed_at, p.reviewer_notes
           FROM progress p
           WHERE p.user_id = ?
           ORDER BY p.completed_at DESC""",
        (user_id,),
    ).fetchall()

    # ---- Trust metrics ----
    # 1) Time-on-page ratio: actual seconds vs estimated hours, averaged across submitted lessons
    total_actual_seconds = 0
    total_expected_seconds = 0
    burst_24h = 0  # lessons completed in any rolling 24h window — flagged if very high
    fast_completions = 0  # completions where seconds_at_complete < min_seconds (shouldn't happen but track)

    completed_keys_with_meta = []
    for r in completed_rows:
        key = r["lesson_key"]
        try:
            pid, mi, li = key.split("::")
            _, _, lesson = find_lesson(pid, int(mi), int(li))
        except (ValueError, KeyError):
            continue
        if not lesson:
            continue
        reqs = lesson_requirements(lesson)
        secs = r["seconds_at_complete"] or 0
        total_actual_seconds += secs
        total_expected_seconds += reqs["min_seconds"]
        if secs < reqs["min_seconds"]:
            fast_completions += 1
        completed_keys_with_meta.append({
            "lesson_key": key,
            "lesson_title": lesson.get("title", key),
            "expected_min_seconds": reqs["min_seconds"],
            "actual_seconds": secs,
            "completed_at": r["completed_at"],
            "status": r["status"],
            "reflection": r["reflection"] or "",
            "submission_url": r["submission_url"] or "",
            "submission_notes": r["submission_notes"] or "",
            "reviewed_at": r["reviewed_at"],
            "reviewer_notes": r["reviewer_notes"] or "",
        })

    trust_ratio = round(100 * total_actual_seconds / total_expected_seconds) if total_expected_seconds else None

    # Burst detection: count completions in last 24h
    last_24h = db.execute(
        """SELECT COUNT(*) AS c FROM progress
           WHERE user_id = ? AND completed_at > datetime('now', '-1 day')""",
        (user_id,),
    ).fetchone()["c"]
    burst_24h = last_24h

    # Counts by status
    pending_count = sum(1 for s in status_map.values() if s == "pending-review")
    rejected_count = sum(1 for s in status_map.values() if s == "rejected")

    return render_template(
        "admin_user.html",
        viewed_user=user,
        phases=CURRICULUM["phases"],
        approved=approved,
        status_map=status_map,
        stats=stats,
        done=done,
        total=total,
        pct=pct,
        completed_rows=completed_keys_with_meta,
        lesson_key_fn=lesson_key,
        trust_ratio=trust_ratio,
        fast_completions=fast_completions,
        burst_24h=burst_24h,
        pending_count=pending_count,
        rejected_count=rejected_count,
    )


@app.route("/admin/user/<int:user_id>/delete", methods=["POST"])
@login_required
@superuser_required
def admin_user_delete(user_id):
    me = current_user()
    if me["id"] == user_id:
        flash("You cannot delete yourself.", "error")
        return redirect(url_for("admin_dashboard"))
    db = get_db()
    db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    db.commit()
    flash("User deleted.", "info")
    return redirect(url_for("admin_dashboard"))


@app.route("/api/health")
def health():
    return jsonify({"ok": True, "lessons_total": len(all_lesson_keys())})


# -------------------- Boot --------------------

with app.app_context():
    init_db()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)

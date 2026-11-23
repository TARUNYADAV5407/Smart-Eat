#Run:  python app.py      ->  http://127.0.0.1:5000#
import csv
import hashlib
import html
import os
import re
import secrets
import smtplib
import sqlite3
import ssl
import threading
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from functools import wraps
from pathlib import Path

from flask import Flask, g, jsonify, redirect, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent


def load_dotenv():
    """Tiny .env reader (KEY=value lines). Real environment variables win."""
    path = BASE_DIR / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_dotenv()

PAGES_DIR = BASE_DIR / "pages"
CSV_PATH = BASE_DIR / "data" / "D:\smart food tracker\smart_eat\nutrients_clean_final.csv"
DB_PATH = Path(os.environ.get("DATABASE", BASE_DIR / "smart_eat.db"))
SECRET_FILE = BASE_DIR / ".secret_key"

NUTRIENTS = ("calories", "protein", "fat", "sat_fat", "fiber", "carbs")
MIN_PASSWORD_LEN = 8
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def load_secret_key():
    """Use SECRET_KEY from the environment, else create one and keep it in .secret_key."""
    key = os.environ.get("SECRET_KEY")
    if key:
        return key
    if not SECRET_FILE.exists():
        SECRET_FILE.write_text(secrets.token_hex(32))
        try:
            SECRET_FILE.chmod(0o600)
        except OSError:
            pass
    return SECRET_FILE.read_text().strip()


app = Flask(__name__, static_folder=None)
app.config.update(
    SECRET_KEY=load_secret_key(),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE") == "1",  # set to 1 behind HTTPS
    PERMANENT_SESSION_LIFETIME=timedelta(days=7),
    MAX_CONTENT_LENGTH=64 * 1024,
)

# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS foods (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    measure    TEXT NOT NULL,          -- e.g. "1 cup" (what one serving is)
    grams      REAL NOT NULL,          -- weight of one serving
    calories   REAL NOT NULL DEFAULT 0,  -- all nutrients are PER SERVING
    protein    REAL NOT NULL DEFAULT 0,
    fat        REAL NOT NULL DEFAULT 0,
    sat_fat    REAL NOT NULL DEFAULT 0,
    fiber      REAL NOT NULL DEFAULT 0,
    carbs      REAL NOT NULL DEFAULT 0,
    category   TEXT,
    is_custom  INTEGER NOT NULL DEFAULT 0,
    created_by INTEGER REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_foods_name ON foods(name);
CREATE INDEX IF NOT EXISTS idx_foods_category ON foods(category);

CREATE TABLE IF NOT EXISTS food_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    food_id    INTEGER REFERENCES foods(id) ON DELETE SET NULL,
    food_name  TEXT NOT NULL,
    quantity   REAL NOT NULL,
    unit       TEXT NOT NULL,          -- 'grams' | 'servings'
    calories   REAL NOT NULL,          -- snapshot of what was eaten
    protein    REAL NOT NULL,
    fat        REAL NOT NULL,
    fiber      REAL NOT NULL,
    carbs      REAL NOT NULL,
    log_date   TEXT NOT NULL,          -- YYYY-MM-DD (user's local day)
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_log_user_date ON food_log(user_id, log_date);

CREATE TABLE IF NOT EXISTS password_resets (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    used       INTEGER NOT NULL DEFAULT 0
);
"""


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


# --------------------------------------------------------------------------
# CSV import (cleaning the dataset)
# --------------------------------------------------------------------------
def to_num(value):
    """Clean one numeric CSV cell.
    '1,419' -> 1419 | '8-44' -> 26 (midpoint) | 't' (trace) / junk -> 0 | blank -> None
    """
    v = (value or "").strip().replace(",", "")
    if v == "":
        return None
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)", v)
    if m:
        return (float(m[1]) + float(m[2])) / 2
    try:
        return float(v)
    except ValueError:
        return 0.0


def clean_text(value):
    return re.sub(r"\s+", " ", (value or "").replace("\xa0", " ")).strip()


def looks_corrupt(row):
    """Some CSV rows hold placeholder values such as 112,113,114,115,... (each +1).
    Those are not real nutrition data, so they are skipped."""
    cols = ("Grams", "Calories", "Protein", "Fat", "Sat.Fat", "Fiber", "Carbs")
    nums = [to_num(row.get(c)) for c in cols]
    return None not in nums and all(b == a + 1 for a, b in zip(nums, nums[1:]))


def import_foods(db):
    """(Re)load the dataset rows. User-created custom foods are kept.
    Returns (rows_imported, names_of_skipped_rows)."""
    db.execute("DELETE FROM foods WHERE is_custom = 0")
    count, skipped = 0, []
    with open(CSV_PATH, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            name = clean_text(row.get("Food"))
            if not name:
                continue
            if looks_corrupt(row):
                skipped.append(f"{name} ({clean_text(row.get('Measure'))})")
                continue
            grams = to_num(row.get("Grams")) or 0.0
            protein = to_num(row.get("Protein")) or 0.0
            fat = to_num(row.get("Fat")) or 0.0
            carbs = to_num(row.get("Carbs")) or 0.0
            calories = to_num(row.get("Calories"))
            if calories is None:  # missing in the CSV -> estimate from macros
                calories = 4 * protein + 4 * carbs + 9 * fat
            db.execute(
                """INSERT INTO foods
                   (name, measure, grams, calories, protein, fat, sat_fat, fiber, carbs, category)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    name,
                    clean_text(row.get("Measure")) or "1 serving",
                    grams,
                    round(calories, 2),
                    round(protein, 2),
                    round(fat, 2),
                    round(to_num(row.get("Sat.Fat")) or 0.0, 2),
                    round(to_num(row.get("Fiber")) or 0.0, 2),
                    round(carbs, 2),
                    clean_text(row.get("Category")) or None,
                ),
            )
            count += 1
    db.commit()
    return count, skipped


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    has_data = db.execute("SELECT 1 FROM foods WHERE is_custom = 0 LIMIT 1").fetchone()
    if not has_data and CSV_PATH.exists():
        n, skipped = import_foods(db)
        print(f"Imported {n} foods from {CSV_PATH.name}")
        if skipped:
            print("Skipped rows with corrupted placeholder values:", ", ".join(skipped))
    db.commit()
    db.close()


@app.cli.command("reimport-foods")
def reimport_foods_cmd():
    """Reload the foods table from the CSV:  flask --app app reimport-foods"""
    n, skipped = import_foods(get_db())
    print(f"Imported {n} foods; skipped corrupted rows: {skipped}")


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
class ValidationError(Exception):
    pass


@app.errorhandler(ValidationError)
def handle_validation(err):
    return jsonify(error=str(err)), 400


def json_error(message, status=400):
    return jsonify(error=message), status


def body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def number(data, key, default=None, lo=0.0, hi=100000.0):
    raw = data.get(key, default)
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ValidationError(f"'{key}' must be a number.")
    if not (lo <= value <= hi):  # also rejects NaN
        raise ValidationError(f"'{key}' must be between {lo:g} and {hi:g}.")
    return value


def clamp_int(raw, lo, hi, default):
    try:
        return max(lo, min(hi, int(raw)))
    except (TypeError, ValueError):
        return default


def parse_day(raw):
    if not raw:
        return date.today().isoformat()
    try:
        return date.fromisoformat(str(raw)).isoformat()
    except ValueError:
        raise ValidationError("Invalid date. Use YYYY-MM-DD.")


def like_escape(text):
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def utc_now():
    return datetime.now(timezone.utc)


def stamp(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def mail_configured():
    return bool(os.environ.get("SMTP_USER") and os.environ.get("SMTP_PASSWORD"))


def _send_email_now(to, subject, text, html_body):
    user = os.environ["SMTP_USER"]
    password = os.environ["SMTP_PASSWORD"].replace(" ", "")  # Google shows app passwords with spaces
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "587"))

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.environ.get("MAIL_FROM") or f"Smart Eat <{user}>"
    msg["To"] = to
    msg.set_content(text)
    msg.add_alternative(html_body, subtype="html")

    try:
        context = ssl.create_default_context()
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=20, context=context) as smtp:
                smtp.login(user, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=20) as smtp:
                smtp.starttls(context=context)
                smtp.login(user, password)
                smtp.send_message(msg)
        app.logger.info("Email sent to %s", to)
    except Exception:
        app.logger.exception("Could not send email to %s", to)


def send_email(to, subject, text, html_body):
    """Send in a background thread so the page does not wait for the mail server."""
    thread = threading.Thread(target=_send_email_now, args=(to, subject, text, html_body), daemon=True)
    thread.start()
    return thread


def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return get_db().execute("SELECT id, name, email FROM users WHERE id = ?", (uid,)).fetchone()


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        user = current_user()
        if user is None:
            session.clear()
            return json_error("Please log in first.", 401)
        g.user = user
        return view(*args, **kwargs)

    return wrapper


def food_to_dict(row):
    d = dict(row)
    grams = d["grams"] or 0
    d["per_100g"] = (
        {k: round(d[k] * 100 / grams, 1) for k in NUTRIENTS} if grams > 0 else None
    )
    d["is_custom"] = bool(d["is_custom"])
    d.pop("created_by", None)
    return d


@app.after_request
def security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    if request.path.startswith("/api/") or request.path in ("/dashboard.html", "/food.html"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


# --------------------------------------------------------------------------
# Pages  (URLs match the links already used in your HTML files)
# --------------------------------------------------------------------------
PAGES = {
    "homepage": "homepage.html",
    "about_page": "about_page.html",
    "about page": "about_page.html",  # old link with a space
    "feat": "feat.html",
    "login": "login.html",
    "signup": "signup.html",
    "for": "for.html",
    "reset_password": "reset_password.html",
    "dashboard": "food.html",  # login.html redirects to dashboard.html
    "food": "food.html",
}


@app.get("/")
def home():
    return send_from_directory(PAGES_DIR, "homepage.html")


@app.get("/<name>.html")
def page(name):
    filename = PAGES.get(name)
    if filename is None:
        return "Page not found", 404
    if name in ("dashboard", "food") and current_user() is None:
        return redirect("/login.html")
    return send_from_directory(PAGES_DIR, filename)


# --------------------------------------------------------------------------
# Auth API
# --------------------------------------------------------------------------
@app.post("/api/signup")
def signup():
    data = body()
    name = str(data.get("name", "")).strip()
    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", ""))

    if not name or len(name) > 100:
        return json_error("Please enter your name (max 100 characters).")
    if not EMAIL_RE.match(email) or len(email) > 254:
        return json_error("Please enter a valid email address.")
    if len(password) < MIN_PASSWORD_LEN:
        return json_error(f"Password must be at least {MIN_PASSWORD_LEN} characters.")

    db = get_db()
    try:
        cur = db.execute(
            "INSERT INTO users (name, email, password_hash) VALUES (?,?,?)",
            (name, email, generate_password_hash(password)),
        )
        db.commit()
    except sqlite3.IntegrityError:
        return json_error("An account with this email already exists.", 409)

    session.clear()
    session["user_id"] = cur.lastrowid
    session.permanent = True
    return jsonify(message="Account created.", user={"id": cur.lastrowid, "name": name, "email": email}), 201


@app.post("/api/login")
def login():
    data = body()
    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", ""))

    user = get_db().execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if user is None or not check_password_hash(user["password_hash"], password):
        return json_error("Incorrect email or password.", 401)

    session.clear()
    session["user_id"] = user["id"]
    session.permanent = True
    return jsonify(message="Logged in.", user={"id": user["id"], "name": user["name"], "email": user["email"]})


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(message="Logged out.")


@app.get("/api/me")
@login_required
def me():
    u = g.user
    return jsonify(id=u["id"], name=u["name"], email=u["email"])


@app.post("/api/forgot-password")
def forgot_password():
    email = str(body().get("email", "")).strip().lower()
    db = get_db()
    user = db.execute("SELECT id, name FROM users WHERE email = ?", (email,)).fetchone()
    if user:
        token = secrets.token_urlsafe(32)
        db.execute("UPDATE password_resets SET used = 1 WHERE user_id = ?", (user["id"],))
        db.execute(
            "INSERT INTO password_resets (user_id, token_hash, expires_at) VALUES (?,?,?)",
            (user["id"], hashlib.sha256(token.encode()).hexdigest(), stamp(utc_now() + timedelta(hours=1))),
        )
        db.commit()
        base = os.environ.get("APP_BASE_URL", request.host_url.rstrip("/"))
        link = f"{base}/reset_password.html?token={token}"
        if mail_configured():
            first = user["name"].split()[0] if user["name"] else "there"
            send_email(
                email,
                "Reset your Smart Eat password",
                f"Hi {first},\n\nUse this link to set a new password (valid for 1 hour):\n{link}\n\n"
                "If you did not ask for this, you can ignore this email.\n\n- Smart Eat",
                f"""<div style="font-family:Arial,sans-serif;max-width:480px;margin:auto;color:#202820">
                <h2 style="color:#087b27">🌿 SMART EAT</h2>
                <p>Hi {html.escape(first)},</p>
                <p>Click the button below to set a new password. The link is valid for 1 hour.</p>
                <p><a href="{link}" style="background:#087b0b;color:#fff;padding:12px 24px;border-radius:8px;
                   text-decoration:none;font-weight:bold;display:inline-block">Reset Password</a></p>
                <p style="color:#666;font-size:13px">If the button does not work, copy this link:<br>{link}</p>
                <p style="color:#666;font-size:13px">If you did not ask for this, you can ignore this email.</p>
                </div>""",
            )
        else:  # email not configured -> development fallback
            app.logger.warning("Email not configured. PASSWORD RESET LINK for %s: %s", email, link)
    # Same answer whether or not the email exists (prevents account enumeration).
    return jsonify(message="If that email is registered, a password reset link has been sent.")


@app.post("/api/reset-password")
def reset_password():
    data = body()
    token = str(data.get("token", ""))
    password = str(data.get("password", ""))
    if len(password) < MIN_PASSWORD_LEN:
        return json_error(f"Password must be at least {MIN_PASSWORD_LEN} characters.")

    db = get_db()
    row = db.execute(
        "SELECT id, user_id FROM password_resets WHERE token_hash = ? AND used = 0 AND expires_at > ?",
        (hashlib.sha256(token.encode()).hexdigest(), stamp(utc_now())),
    ).fetchone()
    if row is None:
        return json_error("This reset link is invalid or has expired.", 400)

    db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(password), row["user_id"]))
    db.execute("UPDATE password_resets SET used = 1 WHERE user_id = ?", (row["user_id"],))
    db.commit()
    return jsonify(message="Password updated. You can log in now.")


# --------------------------------------------------------------------------
# Foods API (the dataset)
# --------------------------------------------------------------------------
VISIBLE = "(is_custom = 0 OR created_by = ?)"  # dataset foods + the user's own custom foods


@app.get("/api/foods")
def list_foods():
    uid = session.get("user_id") or -1
    q = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip()
    limit = clamp_int(request.args.get("limit"), 1, 100, 25)
    offset = clamp_int(request.args.get("offset"), 0, 10**6, 0)

    where, params = [VISIBLE], [uid]
    if q:
        where.append("name LIKE ? ESCAPE '\\'")
        params.append(f"%{like_escape(q)}%")
    if category:
        where.append("category = ?")
        params.append(category)
    clause = " AND ".join(where)

    db = get_db()
    total = db.execute(f"SELECT COUNT(*) FROM foods WHERE {clause}", params).fetchone()[0]
    if q:  # exact match first, then "starts with", then "contains"
        order = "ORDER BY CASE WHEN lower(name) = lower(?) THEN 0 WHEN name LIKE ? ESCAPE '\\' THEN 1 ELSE 2 END, name"
        order_params = [q, f"{like_escape(q)}%"]
    else:
        order, order_params = "ORDER BY name", []
    rows = db.execute(
        f"SELECT * FROM foods WHERE {clause} {order} LIMIT ? OFFSET ?",
        params + order_params + [limit, offset],
    ).fetchall()
    return jsonify(total=total, foods=[food_to_dict(r) for r in rows])


@app.get("/api/foods/<int:food_id>")
def get_food(food_id):
    uid = session.get("user_id") or -1
    row = get_db().execute(f"SELECT * FROM foods WHERE id = ? AND {VISIBLE}", (food_id, uid)).fetchone()
    if row is None:
        return json_error("Food not found.", 404)
    return jsonify(food_to_dict(row))


@app.get("/api/categories")
def categories():
    rows = get_db().execute(
        "SELECT category, COUNT(*) AS n FROM foods WHERE is_custom = 0 AND category IS NOT NULL "
        "GROUP BY category ORDER BY category"
    ).fetchall()
    return jsonify(categories=[{"name": r["category"], "count": r["n"]} for r in rows])


@app.post("/api/foods")
@login_required
def add_food():
    """Add a custom food (nutrient values are per serving). Visible only to its creator."""
    data = body()
    name = str(data.get("name", "")).strip()
    if not name or len(name) > 120:
        raise ValidationError("Food name is required (max 120 characters).")
    grams = number(data, "grams", 100.0, lo=0.1)
    cols = {k: number(data, k, 0.0) for k in NUTRIENTS}
    measure = str(data.get("measure") or f"{grams:g} g").strip()[:40]
    category = str(data.get("category") or "").strip()[:60] or "Custom"

    db = get_db()
    cur = db.execute(
        """INSERT INTO foods (name, measure, grams, calories, protein, fat, sat_fat, fiber, carbs,
                              category, is_custom, created_by)
           VALUES (?,?,?,?,?,?,?,?,?,?,1,?)""",
        (name, measure, grams, cols["calories"], cols["protein"], cols["fat"], cols["sat_fat"],
         cols["fiber"], cols["carbs"], category, g.user["id"]),
    )
    db.commit()
    row = db.execute("SELECT * FROM foods WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(food_to_dict(row)), 201


# --------------------------------------------------------------------------
# Food log API (what the dashboard "Add Food" form uses)
# --------------------------------------------------------------------------
def find_food(db, name, uid):
    """Best match for a typed name: exact, then starts-with, then contains."""
    esc = like_escape(name)
    for sql, param in (
        (f"SELECT * FROM foods WHERE {VISIBLE} AND lower(name) = lower(?) ORDER BY is_custom DESC, id LIMIT 1", name),
        (f"SELECT * FROM foods WHERE {VISIBLE} AND name LIKE ? ESCAPE '\\' ORDER BY length(name), id LIMIT 1", f"{esc}%"),
        (f"SELECT * FROM foods WHERE {VISIBLE} AND name LIKE ? ESCAPE '\\' ORDER BY length(name), id LIMIT 1", f"%{esc}%"),
    ):
        row = db.execute(sql, (uid, param)).fetchone()
        if row:
            return row
    return None


@app.post("/api/log")
@login_required
def add_log():
    data = body()
    db, uid = get_db(), g.user["id"]

    food = None
    if data.get("food_id") not in (None, ""):
        try:
            food_id = int(data["food_id"])
        except (TypeError, ValueError):
            raise ValidationError("Invalid food_id.")
        food = db.execute(f"SELECT * FROM foods WHERE id = ? AND {VISIBLE}", (food_id, uid)).fetchone()
    else:
        name = str(data.get("food_name", "")).strip()
        if not name:
            raise ValidationError("Please enter a food name.")
        food = find_food(db, name, uid)
    if food is None:
        return json_error("Food not found in the dataset. Start typing to see matching foods.", 404)

    quantity = number(data, "quantity", None, lo=0.0, hi=10000.0)
    if not quantity:
        raise ValidationError("Quantity must be greater than 0.")
    unit = {"grams": "grams", "servings": "servings", "pieces": "servings"}.get(str(data.get("unit", "grams")))
    if unit is None:
        raise ValidationError("Unit must be 'grams' or 'servings'.")

    if unit == "grams":
        if food["grams"] <= 0:
            raise ValidationError("This food has no gram weight in the dataset. Use servings instead.")
        factor = quantity / food["grams"]
    else:
        factor = quantity  # 1 serving = the dataset's 'Measure' (e.g. 1 cup)

    vals = {k: round(food[k] * factor, 1) for k in ("calories", "protein", "fat", "fiber", "carbs")}
    day = parse_day(data.get("date"))
    cur = db.execute(
        """INSERT INTO food_log (user_id, food_id, food_name, quantity, unit,
                                 calories, protein, fat, fiber, carbs, log_date)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (uid, food["id"], food["name"], quantity, unit,
         vals["calories"], vals["protein"], vals["fat"], vals["fiber"], vals["carbs"], day),
    )
    db.commit()
    return jsonify(id=cur.lastrowid, food=food["name"], date=day, **vals), 201


@app.get("/api/log")
@login_required
def get_log():
    day = parse_day(request.args.get("date"))
    rows = get_db().execute(
        "SELECT id, food_name AS name, quantity, unit, calories, protein, carbs, fat, fiber "
        "FROM food_log WHERE user_id = ? AND log_date = ? ORDER BY id",
        (g.user["id"], day),
    ).fetchall()
    return jsonify(date=day, entries=[dict(r) for r in rows])


@app.delete("/api/log/<int:entry_id>")
@login_required
def delete_log(entry_id):
    db = get_db()
    cur = db.execute("DELETE FROM food_log WHERE id = ? AND user_id = ?", (entry_id, g.user["id"]))
    db.commit()
    if cur.rowcount == 0:
        return json_error("Entry not found.", 404)
    return jsonify(message="Deleted.")


# --------------------------------------------------------------------------
# Dashboard + analysis
# --------------------------------------------------------------------------
@app.get("/api/dashboard")
@login_required
def dashboard():
    day = parse_day(request.args.get("date"))
    db, uid = get_db(), g.user["id"]

    entries = [dict(r) for r in db.execute(
        "SELECT id, food_name AS name, quantity, unit, calories, protein, carbs, fat, fiber "
        "FROM food_log WHERE user_id = ? AND log_date = ? ORDER BY id", (uid, day))]
    totals = {k: round(sum(e[k] for e in entries), 1) for k in ("calories", "protein", "carbs", "fat", "fiber")}

    end = date.fromisoformat(day)
    start = end - timedelta(days=6)
    per_day = {r["log_date"]: r["kcal"] for r in db.execute(
        "SELECT log_date, SUM(calories) AS kcal FROM food_log "
        "WHERE user_id = ? AND log_date BETWEEN ? AND ? GROUP BY log_date",
        (uid, start.isoformat(), end.isoformat()))}
    weekly = []
    for i in range(7):
        d = start + timedelta(days=i)
        weekly.append({"date": d.isoformat(), "label": d.strftime("%a"),
                       "calories": round(per_day.get(d.isoformat(), 0))})

    return jsonify(user={"name": g.user["name"]}, date=day, entries=entries, totals=totals, weekly=weekly)


@app.get("/api/analysis/top")
def top_foods():
    """Top foods by a nutrient, normalised per 100 g so foods are comparable."""
    nutrient = request.args.get("nutrient", "protein")
    if nutrient not in NUTRIENTS:
        return json_error(f"nutrient must be one of: {', '.join(NUTRIENTS)}")
    limit = clamp_int(request.args.get("limit"), 1, 50, 10)
    category = request.args.get("category", "").strip()

    sql = f"SELECT name, category, measure, {nutrient} * 100.0 / grams AS per_100g FROM foods WHERE is_custom = 0 AND grams > 0"
    params = []
    if category:
        sql += " AND category = ?"
        params.append(category)
    rows = get_db().execute(sql + " ORDER BY per_100g DESC LIMIT ?", params + [limit]).fetchall()
    return jsonify(nutrient=nutrient, unit="kcal per 100 g" if nutrient == "calories" else "g per 100 g",
                   foods=[{"name": r["name"], "category": r["category"], "measure": r["measure"],
                           "per_100g": round(r["per_100g"], 1)} for r in rows])


@app.get("/api/analysis/categories")
def category_summary():
    """Average nutrition per 100 g for each food category (for charts)."""
    rows = get_db().execute(
        "SELECT category, COUNT(*) AS foods, AVG(calories*100.0/grams) AS calories, "
        "AVG(protein*100.0/grams) AS protein, AVG(carbs*100.0/grams) AS carbs, AVG(fat*100.0/grams) AS fat "
        "FROM foods WHERE is_custom = 0 AND grams > 0 AND category IS NOT NULL "
        "GROUP BY category ORDER BY category").fetchall()
    return jsonify(categories=[
        {"category": r["category"], "foods": r["foods"], **{k: round(r[k], 1) for k in ("calories", "protein", "carbs", "fat")}}
        for r in rows])


@app.errorhandler(404)
def not_found(_e):
    if request.path.startswith("/api/"):
        return json_error("Not found.", 404)
    return "Page not found", 404


@app.errorhandler(405)
def bad_method(_e):
    return json_error("Method not allowed.", 405)


init_db()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)), debug=os.environ.get("FLASK_DEBUG") == "1")

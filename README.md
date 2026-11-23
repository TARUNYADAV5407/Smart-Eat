# Smart Eat – Backend (Flask + SQLite)

## Run
```bash
pip install -r requirements.txt
python app.py            # http://127.0.0.1:5000
```
On first start it creates `smart_eat.db` and imports `data/nutrients_csvfile.csv`.
Open http://127.0.0.1:5000 → Get Started → Sign up → dashboard.

## Folder layout
```
smart_eat/
├── app.py                  # whole backend
├── requirements.txt
├── data/nutrients_csvfile.csv
└── pages/                  # your HTML, patched to call the API
    ├── homepage.html  about_page.html  feat.html
    ├── login.html  signup.html  for.html  reset_password.html (new)
    └── food.html           # served as /dashboard.html
```

## API
| Method | URL | Auth | Purpose |
|---|---|---|---|
| POST | /api/signup | – | `{name,email,password}` (min 8 chars), logs user in |
| POST | /api/login | – | `{email,password}` |
| POST | /api/logout | – | |
| GET  | /api/me | yes | current user |
| POST | /api/forgot-password | – | `{email}` – emails a reset link (or prints it in the console if email is not set up) |
| POST | /api/reset-password | – | `{token,password}` |
| GET  | /api/foods?q=&category=&limit=&offset= | – | search dataset (incl. your custom foods) |
| GET  | /api/foods/<id> | – | one food |
| GET  | /api/categories | – | 16 categories with counts |
| POST | /api/foods | yes | add a private custom food (values per serving) |
| POST | /api/log | yes | `{food_id or food_name, quantity, unit: grams or servings, date}` |
| GET  | /api/log?date= | yes | entries for a day |
| DELETE | /api/log/<id> | yes | remove an entry |
| GET  | /api/dashboard?date= | yes | entries, totals, last-7-days calories |
| GET  | /api/analysis/top?nutrient=protein&limit=10 | – | top foods per 100 g |
| GET  | /api/analysis/categories | – | average nutrition per category (for charts) |

## How the dataset is used
* Every nutrient in the CSV is **per serving** (`Measure`, weighing `Grams`).
* Logging in **grams** -> `value x grams_eaten / Grams`. Logging in **servings** -> `value x servings`.
* Cleaning on import: `t` (trace) -> 0, `1,419` -> 1419, `8-44` -> 26 (midpoint), blank calories -> estimated from macros,
  3 rows with placeholder numbers (Butter x2, Oysters) are skipped.
* Reload the CSV any time: `flask --app app reimport-foods`

## Before deploying
* Set `SECRET_KEY` (otherwise one is generated into `.secret_key`), `COOKIE_SECURE=1` (HTTPS), `APP_BASE_URL`.
* Run with a real server: `pip install gunicorn && gunicorn app:app`.

## Email setup (password reset)
1. Google Account -> Security -> turn on **2-Step Verification**.
2. Go to https://myaccount.google.com/apppasswords and create an App Password (16 characters).
3. Copy `.env.example` to `.env` and fill in `SMTP_USER` and `SMTP_PASSWORD`.
4. Restart `python app.py`. The reset email is now sent for real.

Without a `.env`, the reset link is simply printed in the server console. Keep `.env` private.

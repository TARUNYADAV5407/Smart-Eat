"""
Run this inside your smart_eat folder:   python smtp_test.py
It reads your .env, tries Gmail on port 587 and 465, and tells you what is wrong.
"""
import os
import smtplib
import ssl
from pathlib import Path

env = Path(__file__).with_name(".env")
if not env.exists():
    raise SystemExit("No .env file found next to this script. Create it from .env.example first.")

for line in env.read_text(encoding="utf-8-sig").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

user = os.environ.get("SMTP_USER", "")
password = os.environ.get("SMTP_PASSWORD", "").replace(" ", "")
host = os.environ.get("SMTP_HOST", "smtp.gmail.com")

print(f"Email    : {user!r}")
print(f"Password : {len(password)} characters (a Gmail App Password must be exactly 16 letters)")
if not user or not password:
    raise SystemExit("SMTP_USER / SMTP_PASSWORD missing in .env")
if len(password) != 16:
    print("WARNING: this does not look like a 16-character App Password.\n")

ctx = ssl.create_default_context()


def attempt(port):
    print(f"\n--- Trying {host}:{port} ---")
    try:
        if port == 465:
            smtp = smtplib.SMTP_SSL(host, port, timeout=20, context=ctx)
        else:
            smtp = smtplib.SMTP(host, port, timeout=20)
            smtp.starttls(context=ctx)
        with smtp:
            smtp.login(user, password)
        print(f"SUCCESS: logged in on port {port}.")
        return True
    except smtplib.SMTPAuthenticationError as e:
        print("FAILED: Gmail rejected the login (wrong or revoked App Password, or wrong account).")
        print("  ", e)
    except smtplib.SMTPServerDisconnected as e:
        print("FAILED: connection closed during login (antivirus / firewall / network blocking it).")
        print("  ", e)
    except Exception as e:
        print(f"FAILED: {type(e).__name__}: {e}")
    return False


ok587 = attempt(587)
ok465 = attempt(465)

print("\n=== Result ===")
if ok587:
    print("Port 587 works. Your .env is fine; just restart `python app.py`.")
elif ok465:
    print("Port 465 works but 587 does not.")
    print("Add this line to your .env and restart the app:   SMTP_PORT=465")
else:
    print("Neither port worked. Fix in this order:")
    print("1. Create a NEW App Password at https://myaccount.google.com/apppasswords and paste it into .env")
    print("2. Make sure SMTP_USER is the same Google account that created it.")
    print("3. Temporarily turn off your antivirus 'email/web shield' and run this again.")
    print("4. Try a mobile hotspot (some Wi-Fi networks block email ports).")

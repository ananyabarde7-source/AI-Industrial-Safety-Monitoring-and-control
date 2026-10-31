"""Reference reconstruction of the project's Mac-to-Firebase cloud logger.

The conversation artifacts showed this workflow and config fields, but did not
include the complete original cloud_logger.py. This is a documented reference
implementation, not a byte-for-byte copy of the original file. Check your
Firebase Realtime Database rules/path before use.
"""
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from urllib.parse import quote

import requests

CONFIG_FILE = "config.json"
STATE_FILE = "upload_state.json"
POLL_SECONDS = 5

with open(CONFIG_FILE, "r", encoding="utf-8") as f:
    config = json.load(f)

API_KEY = config["firebase_api_key"]
DATABASE_URL = config["database_url"].rstrip("/")
EMAIL = config["firebase_email"]
PASSWORD = config["firebase_password"]
PI_HOST = config.get("pi_host", "pi@raspberrypi.local")
PI_LOG_FILE = config.get("pi_log_file", "/home/pi/df/logs/events.log")
DATABASE_PATH = config.get("database_path", "users/{uid}/events")

session = requests.Session()
id_token = None
refresh_token = None
uid = None
token_expiry = 0.0


def authenticate():
    """Sign in using Firebase Authentication's email/password REST endpoint."""
    global id_token, refresh_token, uid, token_expiry
    url = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword"
    response = session.post(
        url,
        params={"key": API_KEY},
        json={"email": EMAIL, "password": PASSWORD, "returnSecureToken": True},
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    id_token = payload["idToken"]
    refresh_token = payload["refreshToken"]
    uid = payload["localId"]
    token_expiry = time.time() + int(payload.get("expiresIn", "3600")) - 60
    print("[FIREBASE] Connected; UID loaded.", flush=True)


def ensure_token():
    global id_token, refresh_token, uid, token_expiry
    if not id_token:
        authenticate()
        return
    if time.time() < token_expiry:
        return
    response = session.post(
        "https://securetoken.googleapis.com/v1/token",
        params={"key": API_KEY},
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    id_token = payload["id_token"]
    refresh_token = payload.get("refresh_token", refresh_token)
    uid = payload.get("user_id", uid)
    token_expiry = time.time() + int(payload.get("expires_in", "3600")) - 60


def get_pi_logs():
    """Read the Pi event log over SSH; SSH key auth is recommended."""
    command = ["ssh", PI_HOST, "cat", PI_LOG_FILE]
    result = subprocess.run(
        command, capture_output=True, text=True, timeout=15, check=True
    )
    return result.stdout.splitlines()


def load_last_line():
    if not os.path.exists(STATE_FILE):
        return 0
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return max(0, int(json.load(f).get("last_line", 0)))
    except (OSError, ValueError, json.JSONDecodeError):
        return 0


def save_last_line(line_number):
    temp_path = STATE_FILE + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump({"last_line": line_number}, f)
    os.replace(temp_path, STATE_FILE)


def upload_event(line):
    ensure_token()
    path = DATABASE_PATH.format(uid=quote(uid, safe=""))
    url = "{}/{}.json".format(DATABASE_URL, path.strip("/"))
    response = session.post(
        url,
        params={"auth": id_token},
        json={
            "raw": line,
            "source": "raspberry_pi_events_log",
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
        },
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def main():
    print("\n======================================")
    print(" VIRTUAL FENCING CLOUD LOGGER (REFERENCE)")
    print("======================================", flush=True)
    authenticate()
    last_line = load_last_line()
    print("[LOGGER] Starting from line: {}".format(last_line), flush=True)

    while True:
        try:
            lines = get_pi_logs()
            # If the log was reset/rotated, restart from its current beginning.
            if last_line > len(lines):
                last_line = 0
            new_lines = lines[last_line:]
            for line in new_lines:
                if not line.strip():
                    last_line += 1
                    save_last_line(last_line)
                    continue
                upload_event(line)
                last_line += 1
                save_last_line(last_line)
                print("[FIREBASE] Uploaded event line {}".format(last_line), flush=True)
        except subprocess.TimeoutExpired:
            print("[PI] Connection error: SSH log read timed out.", flush=True)
        except (subprocess.CalledProcessError, OSError) as exc:
            print("[PI] Connection error: {}".format(exc), flush=True)
        except requests.RequestException as exc:
            print("[FIREBASE] Upload error: {}".format(exc), flush=True)
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            print("[LOGGER] Configuration/data error: {}".format(exc), flush=True)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCloud logger stopped by user.", flush=True)

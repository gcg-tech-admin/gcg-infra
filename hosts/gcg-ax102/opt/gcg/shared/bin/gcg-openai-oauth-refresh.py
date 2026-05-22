#!/usr/bin/env python3
"""
gcg-openai-oauth-refresh.py — OpenAI Codex OAuth token refresh daemon
Task #89 | Built 2026-05-20

Reads the openclaw auth-profile (AES-256-GCM encrypted) to check JWT expiry.
If access_token expires in < 30 min, refreshes via OpenAI OAuth and updates:
  1. /root/.openclaw/credentials/auth-profiles/<hash>.json (openclaw format, re-encrypted)
  2. /etc/credstore.encrypted/openai-codex-auth (systemd-creds format, for bootstrap)
Audits to public.audit_log on success.
"""

import sys
import os
import json
import base64
import hashlib
import time
import urllib.request
import urllib.error
import subprocess
import tempfile
import logging
import logging.handlers
from datetime import datetime, timezone

# ── Constants ─────────────────────────────────────────────────────────────────
PROFILE_HASH      = "5595fd4e0a29ef70b2822dfe0cc40593"
PROFILE_ID        = "openai-codex:peter@global-capital-group.com"
PROVIDER          = "openai-codex"
PROFILE_DIR       = "/root/.openclaw/credentials/auth-profiles"
PROFILE_FILE      = f"{PROFILE_DIR}/{PROFILE_HASH}.json"
SECRET_KEY_FILE   = "/root/.config/openclaw/auth-profile-secret-key"
CREDSTORE_FILE    = "/etc/credstore.encrypted/openai-codex-auth"
ARCHIVE_DIR       = "/opt/gcg/_archived"
LOG_FILE          = "/var/log/gcg/oauth-refresh.log"
LOG_MAX_BYTES     = 1 * 1024 * 1024  # 1 MB
LOG_BACKUP_COUNT  = 3
REFRESH_THRESHOLD = 1800  # 30 min runway triggers refresh
CLIENT_ID         = "app_EMoamEEZ73f0CkXaXp7hrann"
TOKEN_ENDPOINT    = "https://auth.openai.com/oauth/token"
DB_ENV_FILE       = "/run/openclaw-talos/env"


# ── Logging ───────────────────────────────────────────────────────────────────
def setup_logging():
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    root.addHandler(logging.StreamHandler(sys.stderr))


log = logging.getLogger(__name__)


# ── Crypto helpers ────────────────────────────────────────────────────────────
def _b64url_decode(s: str) -> bytes:
    pad = 4 - len(s) % 4
    if pad != 4:
        s += "=" * pad
    return base64.urlsafe_b64decode(s)


def _b64url_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _derive_aes_key(key_seed: bytes) -> bytes:
    """Replicate openclaw buildOAuthProfileSecretKey:
       sha256("openclaw:auth-profile-oauth:" + keyRaw)
    """
    return hashlib.sha256(b"openclaw:auth-profile-oauth:" + key_seed).digest()


def _build_aad() -> bytes:
    """Replicate openclaw buildOAuthProfileSecretAad:
       ref.id + NUL + profileId + NUL + provider
    """
    return f"{PROFILE_HASH}\x00{PROFILE_ID}\x00{PROVIDER}".encode("utf-8")


def decrypt_openclaw_profile(profile_path: str, key_seed: bytes) -> dict:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.backends import default_backend

    with open(profile_path) as f:
        outer = json.load(f)

    enc = outer["encrypted"]
    iv  = _b64url_decode(enc["iv"])
    tag = _b64url_decode(enc["tag"])
    ct  = _b64url_decode(enc["ciphertext"])
    key = _derive_aes_key(key_seed)
    aad = _build_aad()

    cipher = Cipher(algorithms.AES(key), modes.GCM(iv, tag), backend=default_backend())
    dec = cipher.decryptor()
    dec.authenticate_additional_data(aad)
    pt = dec.update(ct) + dec.finalize()
    return json.loads(pt.decode("utf-8"))


def encrypt_openclaw_profile(material: dict, key_seed: bytes) -> dict:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.backends import default_backend

    key = _derive_aes_key(key_seed)
    aad = _build_aad()
    iv  = os.urandom(12)

    cipher = Cipher(algorithms.AES(key), modes.GCM(iv), backend=default_backend())
    enc_ctx = cipher.encryptor()
    enc_ctx.authenticate_additional_data(aad)
    pt_bytes = json.dumps(material).encode("utf-8")
    ct  = enc_ctx.update(pt_bytes) + enc_ctx.finalize()
    tag = enc_ctx.tag

    return {
        "version":   1,
        "profileId": PROFILE_ID,
        "provider":  PROVIDER,
        "encrypted": {
            "algorithm":  "aes-256-gcm",
            "iv":         _b64url_encode(iv),
            "tag":        _b64url_encode(tag),
            "ciphertext": _b64url_encode(ct),
        },
    }


# ── JWT expiry ────────────────────────────────────────────────────────────────
def decode_jwt_exp(token: str) -> int:
    parts = token.split(".")
    if len(parts) < 2:
        raise ValueError("Not a valid JWT")
    payload = json.loads(_b64url_decode(parts[1]).decode("utf-8"))
    return int(payload["exp"])


# ── OAuth refresh ─────────────────────────────────────────────────────────────
def call_refresh_endpoint(refresh_token: str) -> dict:
    body = json.dumps({
        "grant_type":    "refresh_token",
        "refresh_token": refresh_token,
        "client_id":     CLIENT_ID,
    }).encode("utf-8")

    req = urllib.request.Request(
        TOKEN_ENDPOINT,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_txt = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} from token endpoint: {body_txt[:500]}")
    except Exception as e:
        raise RuntimeError(f"Token endpoint call failed: {e}")

    if "error" in data:
        raise RuntimeError(
            f"OAuth error: {data.get('error')} — {data.get('error_description', '')}"
        )
    if "access_token" not in data:
        raise RuntimeError(f"No access_token in response (keys: {list(data.keys())})")
    return data


# ── Atomic file writes ────────────────────────────────────────────────────────
def atomic_write_json(path: str, data: dict, mode: int = 0o600):
    dir_ = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=dir_, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.chmod(tmp, mode)
        os.rename(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def update_credstore(access_token: str, refresh_token: str):
    """Decrypt credstore JSON, update tokens, re-encrypt atomically via systemd-creds."""
    tmp_plain = None
    tmp_enc   = None
    try:
        result = subprocess.run(
            ["systemd-creds", "decrypt", CREDSTORE_FILE, "-"],
            capture_output=True, check=True,
        )
        creds = json.loads(result.stdout.decode("utf-8"))

        if "tokens" not in creds:
            creds["tokens"] = {}
        creds["tokens"]["access_token"]  = access_token
        creds["tokens"]["refresh_token"] = refresh_token
        creds["last_refresh"] = (
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        )

        # Write plaintext to /run (tmpfs), encrypt, wipe
        fd, tmp_plain = tempfile.mkstemp(dir="/run", suffix=".oauth.plain")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(json.dumps(creds).encode("utf-8"))
            os.chmod(tmp_plain, 0o600)

            fd2, tmp_enc = tempfile.mkstemp(
                dir=os.path.dirname(CREDSTORE_FILE), suffix=".tmp"
            )
            os.close(fd2)
            os.unlink(tmp_enc)  # systemd-creds needs path, not open fd

            subprocess.run(
                ["systemd-creds", "encrypt", tmp_plain, tmp_enc],
                check=True,
            )
            os.chmod(tmp_enc, 0o644)
            os.rename(tmp_enc, CREDSTORE_FILE)
            tmp_enc = None
        finally:
            if tmp_plain and os.path.exists(tmp_plain):
                try:
                    sz = os.path.getsize(tmp_plain)
                    with open(tmp_plain, "wb") as f:
                        f.write(b"\x00" * sz)
                    os.unlink(tmp_plain)
                except OSError:
                    pass
                tmp_plain = None
    finally:
        if tmp_enc and os.path.exists(tmp_enc):
            try: os.unlink(tmp_enc)
            except OSError: pass


# ── DB audit ──────────────────────────────────────────────────────────────────
def load_db_env() -> dict:
    db = {}
    if os.path.exists(DB_ENV_FILE):
        with open(DB_ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    db[k.strip()] = v.strip()
    return db


def audit_refresh(new_exp: int, skipped: bool = False):
    try:
        db = load_db_env()
        host    = db.get("GCG_DB_HOST",     "95.217.114.49")
        port    = db.get("GCG_DB_PORT",     "5432")
        dbname  = db.get("GCG_DB_NAME",     "gcg_intelligence")
        user    = db.get("GCG_DB_USER",     "gcg_admin")
        pw      = db.get("GCG_DB_PASSWORD") or db.get("PGPASSWORD", "")
        sslmode = db.get("PGSSLMODE",       "require")

        status = "skipped_not_due" if skipped else "refreshed"
        new_value_json = json.dumps({
            "agent_id":   "system",
            "profile_id": PROFILE_ID,
            "status":     status,
            "new_exp":    new_exp,
        })

        dsn = f"host={host} port={port} dbname={dbname} user={user} sslmode={sslmode}"
        sql = (
            "INSERT INTO public.audit_log (action, table_name, user_agent, new_value) "
            "VALUES ('oauth_refresh', 'auth_profiles', 'gcg-oauth-refresh/system', "
            "'" + new_value_json.replace("'", "''") + "');"
        )

        env = os.environ.copy()
        env["PGPASSWORD"] = pw

        subprocess.run(
            ["psql", dsn, "-c", sql],
            env=env, check=True, capture_output=True,
        )
        log.info(f"audit_log: action=oauth_refresh status={status} new_exp={new_exp}")
    except Exception as e:
        log.warning(f"audit_log write failed (non-fatal): {e}")


# ── Backup ────────────────────────────────────────────────────────────────────
def backup_profile():
    import shutil
    ts   = int(time.time())
    dest = f"{ARCHIVE_DIR}/oauth-profile-pre-daemon.{ts}.json"
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    shutil.copy2(PROFILE_FILE, dest)
    os.chmod(dest, 0o600)
    log.info(f"Backed up auth profile to {dest}")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    setup_logging()
    log.info("=== gcg-openai-oauth-refresh start ===")

    # Load openclaw secret key seed
    try:
        with open(SECRET_KEY_FILE, "rb") as f:
            key_seed = f.read().strip()
    except Exception as e:
        log.error(f"Cannot read secret key from {SECRET_KEY_FILE}: {e}")
        sys.exit(1)

    # Decrypt openclaw auth-profile
    try:
        material = decrypt_openclaw_profile(PROFILE_FILE, key_seed)
    except Exception as e:
        log.error(f"Cannot decrypt openclaw auth profile: {e}")
        sys.exit(1)

    access_token  = material.get("access")
    refresh_token = material.get("refresh")

    if not access_token or not refresh_token:
        log.error(f"Missing access or refresh in decrypted profile (keys: {list(material.keys())})")
        sys.exit(1)

    # Check JWT expiry
    try:
        exp = decode_jwt_exp(access_token)
    except Exception as e:
        log.error(f"Cannot decode JWT exp: {e}")
        sys.exit(1)

    now    = int(time.time())
    runway = exp - now
    log.info(f"Token exp={exp} now={now} runway={runway}s ({runway // 60} min)")

    if runway >= REFRESH_THRESHOLD:
        log.info(f"Token still valid for {runway // 60} min >= {REFRESH_THRESHOLD // 60} min — skipping refresh")
        audit_refresh(exp, skipped=True)
        log.info("=== done (no refresh needed) ===")
        sys.exit(0)

    # Token needs refresh
    log.info(f"Runway {runway}s < {REFRESH_THRESHOLD}s — refreshing token")
    backup_profile()

    try:
        resp = call_refresh_endpoint(refresh_token)
    except RuntimeError as e:
        log.error(f"REFRESH FAILED: {e}")
        sys.exit(1)

    new_access  = resp["access_token"]
    new_refresh = resp.get("refresh_token", refresh_token)  # rotate if provided

    try:
        new_exp = decode_jwt_exp(new_access)
    except Exception as e:
        log.warning(f"Cannot decode new JWT exp: {e} — estimating from expires_in")
        new_exp = now + int(resp.get("expires_in", 3600))

    log.info(f"New token exp={new_exp} (in {(new_exp - now) // 60} min)")

    # 1. Update openclaw AES-GCM profile (live path — agents read this at runtime)
    new_material = dict(material)
    new_material["access"]  = new_access
    new_material["refresh"] = new_refresh
    try:
        new_envelope = encrypt_openclaw_profile(new_material, key_seed)
        atomic_write_json(PROFILE_FILE, new_envelope, mode=0o600)
        log.info(f"Updated openclaw auth profile: {PROFILE_FILE}")
    except Exception as e:
        log.error(f"CRITICAL: Failed to write openclaw profile: {e}")
        sys.exit(1)

    # 2. Update credstore (used by bootstrap on agent restart)
    try:
        update_credstore(new_access, new_refresh)
        log.info(f"Updated credstore: {CREDSTORE_FILE}")
    except Exception as e:
        log.warning(f"credstore update failed (non-fatal — live profile is updated): {e}")

    # 3. Audit
    audit_refresh(new_exp, skipped=False)

    log.info("=== gcg-openai-oauth-refresh SUCCESS ===")


if __name__ == "__main__":
    main()

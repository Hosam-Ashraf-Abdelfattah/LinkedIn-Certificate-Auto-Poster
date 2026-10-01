"""
╔══════════════════════════════════════════════════════════════════╗
║         LinkedIn Certificate Auto-Poster                         ║
║         By: Hosam Ashraf  —  Posts Generator App                 ║
║         Posts every Thursday 9:00 AM (Cairo time)                ║
╚══════════════════════════════════════════════════════════════════╝

INSTALL DEPENDENCIES (one time):
    pip install pymupdf pillow apscheduler requests pytz

FIRST TIME SETUP:
    python linkedin_cert_poster.py --auth       # Opens browser, saves token
    python linkedin_cert_poster.py --status     # Check your queue
    python linkedin_cert_poster.py --now        # Test post immediately
    python linkedin_cert_poster.py              # Start weekly scheduler
"""

# ─────────────────────────────────────────────────────────────────
#  IMPORTS
# ─────────────────────────────────────────────────────────────────
import argparse
import base64
import http.server
import io
import json
import logging
import os
import re
import secrets
import shutil
import sys
import time
import urllib.parse
import webbrowser
from datetime import datetime
from pathlib import Path

import fitz                               # PyMuPDF  — PDF → image + text
import requests
from PIL import Image                     # Pillow   — image optimisation
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from pytz import timezone as pytz_timezone

# ─────────────────────────────────────────────────────────────────
#  CONFIG  (edit these if needed, or they are read from config.json)
# ─────────────────────────────────────────────────────────────────
CERTS_DIR    = Path("/home/hosam/Desktop/projects/linkedIn_posts_maker/certs")
POSTED_DIR   = CERTS_DIR / "posted"
CONFIG_PATH  = Path("/home/hosam/Desktop/projects/linkedIn_posts_maker/config.json")
TOKEN_PATH   = Path("/home/hosam/Desktop/projects/linkedIn_posts_maker/linkedin_token.json")
LOG_PATH     = Path("/home/hosam/Desktop/projects/linkedIn_posts_maker/poster.log")

DEFAULT_CONFIG = {
    "linkedin_client_id":     "xxxxxxxxxxxxxxxx",
    "linkedin_client_secret": "PASTE_YOUR_NEW_SECRET_HERE",
    "linkedin_redirect_uri":  "http://localhost:8080/callback",
    "ollama_model":           "gemma4:e2b",
    "ollama_url":             "http://localhost:11434",
    "schedule_day_of_week":   "thu",
    "schedule_hour":          9,
    "schedule_minute":        0,
    "timezone":               "Africa/Cairo",
    "your_name":              "Hosam Ashraf",
    "post_language":          "English",
}

# ─────────────────────────────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────────────────────────────
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(str(LOG_PATH), encoding="utf-8"),
    ],
)
log = logging.getLogger("cert_poster")


# ═══════════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════════

def load_config() -> dict:
    if not CONFIG_PATH.exists():
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=4), encoding="utf-8")
        log.info("Created config.json at %s", CONFIG_PATH)
        log.info("⚠️  Please paste your NEW LinkedIn Client Secret into config.json first!")
        sys.exit(0)
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    for k, v in DEFAULT_CONFIG.items():
        cfg.setdefault(k, v)
    return cfg


def validate_config(cfg: dict) -> None:
    if not cfg.get("linkedin_client_id"):
        log.error("linkedin_client_id is missing in config.json")
        sys.exit(1)
    if not cfg.get("linkedin_client_secret") or "PASTE_YOUR" in cfg["linkedin_client_secret"]:
        log.error("Please paste your LinkedIn Client Secret into config.json")
        sys.exit(1)


# ═══════════════════════════════════════════════════════════════════
#  CERTIFICATE DISCOVERY
# ═══════════════════════════════════════════════════════════════════

def discover_certificates() -> list[Path]:
    """
    Find all PDF files in CERTS_DIR (not in posted/ subfolder).
    Sorted alphabetically so order is consistent.
    """
    CERTS_DIR.mkdir(parents=True, exist_ok=True)
    POSTED_DIR.mkdir(parents=True, exist_ok=True)
    pdfs = sorted(CERTS_DIR.glob("*.pdf"), key=lambda p: p.name.lower())
    return pdfs


def move_to_posted(pdf_path: Path) -> Path:
    """Move a posted certificate to the posted/ subfolder."""
    dest = POSTED_DIR / pdf_path.name
    # Avoid name collision
    if dest.exists():
        stem  = pdf_path.stem
        suffix = pdf_path.suffix
        dest = POSTED_DIR / f"{stem}_{int(time.time())}{suffix}"
    shutil.move(str(pdf_path), str(dest))
    log.info("Moved '%s' → posted/%s", pdf_path.name, dest.name)
    return dest


# ═══════════════════════════════════════════════════════════════════
#  PDF PROCESSING  (PyMuPDF)
# ═══════════════════════════════════════════════════════════════════

def pdf_to_jpeg_bytes(pdf_path: Path, dpi: int = 200) -> bytes:
    """
    Render the first page of a PDF at given DPI and return JPEG bytes.
    Automatically reduces quality if file exceeds LinkedIn's 5 MB limit.
    """
    doc  = fitz.open(str(pdf_path))
    page = doc[0]
    mat  = fitz.Matrix(dpi / 72, dpi / 72)
    pix  = page.get_pixmap(matrix=mat, alpha=False)
    doc.close()

    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    log.info("Rendered PDF page: %dx%d px", pix.width, pix.height)

    # Compress to under 5 MB (LinkedIn safe limit)
    for quality in [90, 80, 70, 60, 50]:
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        data = buf.getvalue()
        if len(data) <= 5_000_000:
            log.info("Image compressed: %.1f KB (quality=%d)", len(data) / 1024, quality)
            return data

    # Last resort: halve the resolution
    img = img.resize((img.width // 2, img.height // 2), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=70)
    log.info("Image resized to 50%% and compressed.")
    return buf.getvalue()


def extract_text_from_pdf(pdf_path: Path, max_chars: int = 1500) -> str:
    """
    Extract all readable text from all pages of the PDF.
    This gives Ollama rich context about the certificate content.
    """
    doc  = fitz.open(str(pdf_path))
    full_text = ""
    for page in doc:
        full_text += page.get_text("text") + "\n"
    doc.close()
    text = re.sub(r'\n{3,}', '\n\n', full_text).strip()
    return text[:max_chars]


# ═══════════════════════════════════════════════════════════════════
#  OLLAMA — LOCAL LLM
# ═══════════════════════════════════════════════════════════════════

# def generate_post_text(pdf_path: Path, cert_text: str, cfg: dict) -> str:
#     """
#     Send certificate text to Ollama (gemma4:e2b) to generate a LinkedIn post.
#     """
#     cert_name = pdf_path.stem.replace("_", " ").replace("-", " ").title()

#     prompt = f"""You are a professional LinkedIn content writer helping {cfg['your_name']} share a certificate.

# Certificate file name: {cert_name}

# Full text extracted from the certificate PDF:
# \"\"\"
# {cert_text if cert_text else "No text could be extracted — use the certificate name only."}
# \"\"\"

# Write a compelling LinkedIn post in {cfg['post_language']} for {cfg['your_name']} sharing this certificate.

# Rules:
# - Maximum 5 sentences
# - First sentence must be engaging and specific (never start with "I'm happy to share" or "I'm excited")
# - Mention the specific skill or knowledge gained from this certificate
# - Sound human, warm and professional — not robotic
# - End with 4–6 relevant hashtags on a new line
# - Output ONLY the post text — no intro, no "Here is:", no quotes around it

# Post:"""

#     url     = cfg["ollama_url"].rstrip("/") + "/api/generate"
#     payload = {
#         "model":  cfg["ollama_model"],
#         "prompt": prompt,
#         "stream": False,
#         "options": {
#             "temperature": 0.8,
#             "num_predict": 300,
#         },
#     }

#     log.info("Generating post with Ollama (%s)...", cfg["ollama_model"])
#     try:
#         resp = requests.post(url, json=payload, timeout=180)
#         resp.raise_for_status()
#         text = resp.json().get("response", "").strip()
#         if not text:
#             raise ValueError("Ollama returned empty response.")
#         log.info("Post generated (%d chars).", len(text))
#         return text
#     except requests.exceptions.ConnectionError:
#         log.error("Cannot reach Ollama at %s", cfg["ollama_url"])
#         log.error("Run:  ollama serve")
#         raise
#     except Exception as exc:
#         log.error("Ollama error: %s", exc)
#         raise

# def generate_post_text(pdf_path: Path, cert_text: str, cfg: dict) -> str:
#     """
#     Send certificate info to Ollama (gemma4:e2b) to generate a LinkedIn post.
#     Kept short — gemma4:e2b is a small model and returns empty on long prompts.
#     """
#     # Strip the common "CertificateOfCompletion_" prefix for a clean title
#     cert_name = pdf_path.stem
#     for prefix in ["CertificateOfCompletion_", "Certificate_Of_Completion_", "certificate_"]:
#         if cert_name.startswith(prefix):
#             cert_name = cert_name[len(prefix):]
#             break
#     cert_name = cert_name.replace("_", " ").replace("-", " ").strip()

#     # Trim to 300 chars — gemma4:e2b is small, long context = empty response
#     short_text = cert_text[:300].strip() if cert_text else ""

#     # Short, direct prompt — works reliably with small models
#     prompt = (
#         f"Write a short LinkedIn post (3-4 sentences + hashtags) for {cfg['your_name']} "
#         f"who just completed: \"{cert_name}\". "
#         f"{'Context: ' + short_text if short_text else ''} "
#         f"Be specific about skills learned. End with 4 hashtags. "
#         f"Output only the post text, nothing else."
#     )

#     url = cfg["ollama_url"].rstrip("/") + "/api/generate"
#     # payload = {
#     #     "model":  cfg["ollama_model"],
#     #     "prompt": prompt,
#     #     "stream": False,
#     #     "options": {
#     #         "temperature": 0.7,
#     #         "num_predict": 200,
#     #         "num_ctx":     512,
#     #     },
#     # }
#     payload = {
#         "model":  cfg["ollama_model"],
#         "prompt": prompt,
#         "stream": False,
#         "options": {
#             "temperature": 0.7,
#             "num_predict": 400,
#             "num_ctx":     1024,
#         },
#     }
#     log.info("Generating post with Ollama (%s)...", cfg["ollama_model"])
#     try:
#         resp = requests.post(url, json=payload, timeout=180)
#         resp.raise_for_status()
#         result = resp.json()
#         text = result.get("response", "").strip()
#         if not text:
#             log.error("Ollama raw response: %s", result)
#             raise ValueError("Ollama returned empty response.")
#         log.info("Post generated (%d chars).", len(text))
#         return text
#     except requests.exceptions.ConnectionError:
#         log.error("Cannot reach Ollama at %s", cfg["ollama_url"])
#         log.error("Run:  ollama serve")
#         raise
#     except Exception as exc:
#         log.error("Ollama error: %s", exc)
#         raise

# def generate_post_text(pdf_path: Path, cert_text: str, cfg: dict) -> str:
#     """
#     Generate a LinkedIn post using Ollama chat API (gemma4:e2b).
#     Uses /api/chat instead of /api/generate — required for chat-tuned models.
#     """
#     # Strip the common "CertificateOfCompletion_" prefix for a clean title
#     cert_name = pdf_path.stem
#     for prefix in ["CertificateOfCompletion_", "Certificate_Of_Completion_", "certificate_"]:
#         if cert_name.startswith(prefix):
#             cert_name = cert_name[len(prefix):]
#             break
#     cert_name = cert_name.replace("_", " ").replace("-", " ").strip()

#     # Build a minimal, clear message — gemma4:e2b is small, keep it short
#     prompt_msg = (
#         f"Write a LinkedIn post for {cfg['your_name']} who completed \"{cert_name}\". "
#         f"3-4 sentences, professional tone, mention specific skills learned. "
#         f"End with 4 relevant hashtags. Output only the post text, nothing else."
#     )

#     url = cfg["ollama_url"].rstrip("/") + "/api/chat"
#     payload = {
#         "model":  cfg["ollama_model"],
#         "stream": False,
#         "messages": [
#             {"role": "user", "content": prompt_msg}
#         ],
#         "options": {
#             "temperature": 0.7,
#             "num_predict": 300,
#             "num_ctx":     1024,
#         },
#     }

#     log.info("Generating post with Ollama (%s) via /api/chat...", cfg["ollama_model"])
#     try:
#         resp = requests.post(url, json=payload, timeout=180)
#         resp.raise_for_status()
#         result = resp.json()

#         # /api/chat returns message.content, not response
#         text = result.get("message", {}).get("content", "").strip()

#         if not text:
#             log.error("Ollama raw response: %s", result)
#             raise ValueError("Ollama returned empty response.")

#         log.info("Post generated (%d chars).", len(text))
#         return text

#     except requests.exceptions.ConnectionError:
#         log.error("Cannot reach Ollama at %s", cfg["ollama_url"])
#         log.error("Run:  ollama serve")
#         raise
#     except Exception as exc:
#         log.error("Ollama error: %s", exc)
#         raise

def generate_post_text(pdf_path: Path, cert_text: str, cfg: dict) -> str:
    """
    Generate a LinkedIn post using Ollama chat API (gemma4:e2b).
    gemma4:e2b is a thinking model — must disable thinking or give enough tokens.
    """
    # Strip the common "CertificateOfCompletion_" prefix for a clean title
    cert_name = pdf_path.stem
    for prefix in ["CertificateOfCompletion_", "Certificate_Of_Completion_", "certificate_"]:
        if cert_name.startswith(prefix):
            cert_name = cert_name[len(prefix):]
            break
    cert_name = cert_name.replace("_", " ").replace("-", " ").strip()

    prompt_msg = (
        f"Write a LinkedIn post for {cfg['your_name']} who completed \"{cert_name}\". "
        f"3-4 sentences, professional tone, mention specific skills learned. "
        f"End with 4 relevant hashtags. Output only the post text, nothing else."
    )

    url = cfg["ollama_url"].rstrip("/") + "/api/chat"
    payload = {
        "model":  cfg["ollama_model"],
        "stream": False,
        "think":  False,          # disable thinking mode — gemma4:e2b is a reasoning model
        "messages": [
            {"role": "user", "content": prompt_msg}
        ],
        "options": {
            "temperature": 0.7,
            "num_predict": 400,
            "num_ctx":     2048,
        },
    }

    log.info("Generating post with Ollama (%s)...", cfg["ollama_model"])
    try:
        resp = requests.post(url, json=payload, timeout=180)
        resp.raise_for_status()
        result = resp.json()

        # thinking model: content may be empty, actual text is in thinking field
        # with think:false it should be in content directly
        message = result.get("message", {})
        text = message.get("content", "").strip()

        # fallback: if still empty but thinking has content, model ignored think:false
        if not text:
            thinking = message.get("thinking", "")
            log.error("Content empty. Thinking field: %s", thinking[:200])
            log.error("Full response: %s", result)
            raise ValueError("Ollama returned empty response even with think:false.")

        log.info("Post generated (%d chars).", len(text))
        return text

    except requests.exceptions.ConnectionError:
        log.error("Cannot reach Ollama at %s", cfg["ollama_url"])
        log.error("Run:  ollama serve")
        raise
    except Exception as exc:
        log.error("Ollama error: %s", exc)
        raise


# ═══════════════════════════════════════════════════════════════════
#  LINKEDIN OAUTH 2.0
# ═══════════════════════════════════════════════════════════════════

LINKEDIN_AUTH_URL  = "https://www.linkedin.com/oauth/v2/authorization"
LINKEDIN_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
SCOPE              = "openid profile email w_member_social"


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Tiny local HTTP server to capture the OAuth redirect."""
    code  = None
    state = None

    def do_GET(self):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _CallbackHandler.code  = params.get("code",  [None])[0]
        _CallbackHandler.state = params.get("state", [None])[0]
        self.send_response(200)
        self.end_headers()
        self.wfile.write(
            b"<h2 style='font-family:sans-serif;color:green'>"
            b"LinkedIn authorised successfully! You can close this tab.</h2>"
        )

    def log_message(self, *args):
        pass   # silence server logs


def run_oauth_flow(cfg: dict) -> dict:
    state    = secrets.token_urlsafe(16)
    redirect = cfg["linkedin_redirect_uri"]
    port     = int(urllib.parse.urlparse(redirect).port or 8080)

    auth_url = (
        f"{LINKEDIN_AUTH_URL}"
        f"?response_type=code"
        f"&client_id={cfg['linkedin_client_id']}"
        f"&redirect_uri={urllib.parse.quote(redirect, safe='')}"
        f"&state={state}"
        f"&scope={urllib.parse.quote(SCOPE, safe='')}"
    )

    log.info("Opening browser for LinkedIn login...")
    webbrowser.open(auth_url)

    server = http.server.HTTPServer(("localhost", port), _CallbackHandler)
    server.timeout = 120
    log.info("Waiting for LinkedIn callback on port %d (timeout 120s)...", port)
    server.handle_request()

    code = _CallbackHandler.code
    if not code:
        raise RuntimeError("No authorisation code received. Did you approve the app?")
    if _CallbackHandler.state != state:
        raise RuntimeError("State mismatch — possible CSRF issue. Try again.")

    log.info("Exchanging code for access token...")
    resp = requests.post(
        LINKEDIN_TOKEN_URL,
        data={
            "grant_type":    "authorization_code",
            "code":          code,
            "redirect_uri":  redirect,
            "client_id":     cfg["linkedin_client_id"],
            "client_secret": cfg["linkedin_client_secret"],
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    resp.raise_for_status()
    token = resp.json()
    token["obtained_at"] = int(time.time())
    log.info("✅ Token obtained! Expires in %d seconds (~60 days).", token.get("expires_in", 0))
    return token


def get_valid_token(cfg: dict) -> str:
    """Return a valid access token, re-running OAuth if expired or missing."""
    if TOKEN_PATH.exists():
        token    = json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
        obtained = token.get("obtained_at", 0)
        expires  = token.get("expires_in",  0)
        if int(time.time()) < obtained + expires - 300:
            return token["access_token"]
        log.info("Token expired — re-authorising.")

    token = run_oauth_flow(cfg)
    TOKEN_PATH.write_text(json.dumps(token, indent=2), encoding="utf-8")
    return token["access_token"]


# ═══════════════════════════════════════════════════════════════════
#  LINKEDIN API  —  CORRECT UGC ENDPOINTS (from official docs)
# ═══════════════════════════════════════════════════════════════════

API_BASE = "https://api.linkedin.com/v2"

HEADERS_BASE = {
    "X-Restli-Protocol-Version": "2.0.0",
    "Content-Type": "application/json",
}


def get_person_urn(access_token: str) -> str:
    """Get Person URN using OpenID Connect userinfo endpoint."""
    resp = requests.get(
        f"{API_BASE}/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    )
    resp.raise_for_status()
    sub = resp.json().get("sub")
    if not sub:
        raise RuntimeError("Could not retrieve LinkedIn person ID.")
    log.info("Person URN: urn:li:person:%s", sub)
    return sub


def register_image_upload(access_token: str, person_urn: str) -> tuple[str, str]:
    """
    Step 1 of image upload:
    Register upload → returns (uploadUrl, asset_urn).
    Uses /v2/assets?action=registerUpload as per official docs.
    """
    url = f"{API_BASE}/assets?action=registerUpload"
    payload = {
        "registerUploadRequest": {
            "recipes": [
                "urn:li:digitalmediaRecipe:feedshare-image"
            ],
            "owner": f"urn:li:person:{person_urn}",
            "serviceRelationships": [
                {
                    "relationshipType": "OWNER",
                    "identifier":       "urn:li:userGeneratedContent"
                }
            ]
        }
    }
    headers = {**HEADERS_BASE, "Authorization": f"Bearer {access_token}"}
    resp = requests.post(url, json=payload, headers=headers, timeout=20)
    resp.raise_for_status()

    value      = resp.json()["value"]
    upload_url = value["uploadMechanism"][
        "com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest"
    ]["uploadUrl"]
    asset_urn  = value["asset"]
    log.info("Image upload registered. Asset: %s", asset_urn)
    return upload_url, asset_urn


def upload_image_binary(upload_url: str, access_token: str, image_bytes: bytes) -> None:
    """
    Step 2 of image upload:
    PUT the binary image to the upload URL returned in step 1.
    """
    resp = requests.put(
        upload_url,
        data=image_bytes,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type":  "image/jpeg",
        },
        timeout=60,
    )
    resp.raise_for_status()
    log.info("Image binary uploaded successfully (%d KB).", len(image_bytes) // 1024)


def create_ugc_post(
    access_token: str,
    person_urn: str,
    post_text: str,
    asset_urn: str,
    cert_title: str,
) -> str:
    """
    Step 3: Create the LinkedIn post using the UGC Posts API.
    POST /v2/ugcPosts  (as per official docs)
    Returns the post ID.
    """
    url = f"{API_BASE}/ugcPosts"
    payload = {
        "author":         f"urn:li:person:{person_urn}",
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {
                    "text": post_text
                },
                "shareMediaCategory": "IMAGE",
                "media": [
                    {
                        "status":      "READY",
                        "media":       asset_urn,
                        "title": {
                            "text": cert_title
                        },
                        "description": {
                            "text": f"Certificate shared by {person_urn}"
                        }
                    }
                ]
            }
        },
        "visibility": {
            "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
        }
    }
    headers = {**HEADERS_BASE, "Authorization": f"Bearer {access_token}"}
    resp = requests.post(url, json=payload, headers=headers, timeout=30)
    resp.raise_for_status()

    post_id = resp.headers.get("X-RestLi-Id") or resp.headers.get("x-restli-id", "unknown")
    log.info("Post created! ID: %s", post_id)
    return post_id


# ═══════════════════════════════════════════════════════════════════
#  MAIN JOB — full pipeline for one certificate
# ═══════════════════════════════════════════════════════════════════

def run_post_job(cfg: dict) -> bool:
    """
    Full pipeline:
      Discover next PDF → Extract text → Ollama brief →
      Register upload → Upload image → Create UGC post → Move to posted/
    Returns True on success.
    """
    certs = discover_certificates()

    if not certs:
        log.info("🎉 No pending certificates found in %s", CERTS_DIR)
        log.info("   Add more PDFs to continue, or check posted/ for already posted ones.")
        return False

    pdf_path = certs[0]
    cert_title = pdf_path.stem.replace("_", " ").replace("-", " ").title()

    log.info("━" * 60)
    log.info("Next certificate : %s", pdf_path.name)
    log.info("Detected title   : %s", cert_title)
    log.info("Remaining queue  : %d certificate(s)", len(certs))
    log.info("━" * 60)

    try:
        # ── Step 1: Extract text from PDF ────────────────────────
        log.info("[1/5] Reading certificate PDF content...")
        cert_text = extract_text_from_pdf(pdf_path)
        if cert_text:
            log.info("Extracted %d characters from PDF.", len(cert_text))
        else:
            log.warning("No text found in PDF — will use filename for brief.")

        # ── Step 2: Generate post with Ollama ────────────────────
        log.info("[2/5] Generating LinkedIn post with Ollama...")
        post_text = generate_post_text(pdf_path, cert_text, cfg)
        log.info("\n── Generated post ──────────────────────\n%s\n─────────────────────────────────────\n", post_text)

        # ── Step 3: Render PDF page to JPEG ──────────────────────
        log.info("[3/5] Rendering certificate PDF to image...")
        image_bytes = pdf_to_jpeg_bytes(pdf_path)

        # ── Step 4: Upload image to LinkedIn ─────────────────────
        log.info("[4/5] Uploading image to LinkedIn...")
        access_token = get_valid_token(cfg)
        person_urn   = get_person_urn(access_token)
        upload_url, asset_urn = register_image_upload(access_token, person_urn)
        upload_image_binary(upload_url, access_token, image_bytes)

        # ── Step 5: Create the LinkedIn post ─────────────────────
        log.info("[5/5] Publishing post to LinkedIn...")
        post_id = create_ugc_post(
            access_token, person_urn, post_text, asset_urn, cert_title
        )

        # ── Move certificate to posted/ ───────────────────────────
        move_to_posted(pdf_path)

        log.info("✅ SUCCESS — '%s' posted and moved to posted/", pdf_path.name)
        log.info("   Post ID : %s", post_id)
        log.info("   Remaining certificates: %d", len(certs) - 1)
        return True

    except Exception as exc:
        log.error("❌ FAILED to post '%s': %s", pdf_path.name, exc, exc_info=True)
        log.error("   Certificate NOT moved — will retry next scheduled run.")
        return False


# ═══════════════════════════════════════════════════════════════════
#  SCHEDULER
# ═══════════════════════════════════════════════════════════════════

# def start_scheduler(cfg: dict) -> None:
#     cairo_tz  = pytz_timezone(cfg["timezone"])
#     scheduler = BlockingScheduler(timezone=cairo_tz)

#     trigger = CronTrigger(
#         day_of_week = cfg["schedule_day_of_week"],  # thu
#         hour        = cfg["schedule_hour"],          # 9
#         minute      = cfg["schedule_minute"],        # 0
#         timezone    = cairo_tz,
#     )
#     scheduler.add_job(run_post_job, trigger, args=[cfg], id="weekly_cert")

#     jobs    = scheduler.get_jobs()
#     nxt     = jobs[0].next_run_time
#     certs   = discover_certificates()

#     log.info("═" * 60)
#     log.info("  Scheduler started — Every Thursday 9:00 AM Cairo time")
#     log.info("  Next run     : %s", nxt.strftime("%A %d %b %Y at %H:%M %Z"))
#     log.info("  Pending certs: %d PDF(s) in queue", len(certs))
#     log.info("  Press Ctrl+C to stop.")
#     log.info("═" * 60)

#     try:
#         scheduler.start()
#     except (KeyboardInterrupt, SystemExit):
#         log.info("Scheduler stopped.")

def start_scheduler(cfg: dict) -> None:
    cairo_tz  = pytz_timezone(cfg["timezone"])
    scheduler = BlockingScheduler(timezone=cairo_tz)

    trigger = CronTrigger(
        day_of_week = cfg["schedule_day_of_week"],
        hour        = cfg["schedule_hour"],
        minute      = cfg["schedule_minute"],
        timezone    = cairo_tz,
    )
    scheduler.add_job(run_post_job, trigger, args=[cfg], id="weekly_cert")

    certs = discover_certificates()

    log.info("═" * 60)
    log.info("  Scheduler started — Every Thursday 9:00 AM Cairo time")
    log.info("  Pending certs: %d PDF(s) in queue", len(certs))
    log.info("  Press Ctrl+C to stop.")
    log.info("═" * 60)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler stopped.")
# ═══════════════════════════════════════════════════════════════════
#  STATUS REPORT
# ═══════════════════════════════════════════════════════════════════

def print_status() -> None:
    pending = discover_certificates()
    posted  = sorted(POSTED_DIR.glob("*.pdf"), key=lambda p: p.name.lower()) if POSTED_DIR.exists() else []

    print("\n" + "═" * 58)
    print("  LinkedIn Certificate Poster — Queue Status")
    print("═" * 58)
    print(f"  Certs folder  : {CERTS_DIR}")
    print(f"  Posted folder : {POSTED_DIR}")
    print(f"  Pending       : {len(pending)}")
    print(f"  Posted        : {len(posted)}")
    print()

    if pending:
        print("  ⏳  PENDING (will post in this order):")
        for i, p in enumerate(pending, 1):
            title = p.stem.replace("_", " ").replace("-", " ").title()
            print(f"    {i:2}. {title}")
            print(f"        File: {p.name}")
    else:
        print("  No pending certificates — add PDFs to the certs folder.")

    if posted:
        print()
        print("  ✅  ALREADY POSTED:")
        for p in posted:
            title = p.stem.replace("_", " ").replace("-", " ").title()
            print(f"      • {title}")
    print("═" * 58 + "\n")


# ═══════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="LinkedIn Certificate Auto-Poster — Hosam Ashraf",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python linkedin_cert_poster.py --auth     # First time: log in to LinkedIn
  python linkedin_cert_poster.py --status   # See your certificate queue
  python linkedin_cert_poster.py --now      # Post next certificate immediately
  python linkedin_cert_poster.py            # Start weekly Thursday scheduler
        """
    )
    parser.add_argument("--auth",   action="store_true", help="Run LinkedIn OAuth login (saves token)")
    parser.add_argument("--now",    action="store_true", help="Post next certificate immediately")
    parser.add_argument("--status", action="store_true", help="Show queue status")
    args = parser.parse_args()

    cfg = load_config()

    if args.status:
        print_status()
        return

    validate_config(cfg)

    if args.auth:
        token = run_oauth_flow(cfg)
        TOKEN_PATH.write_text(json.dumps(token, indent=2), encoding="utf-8")
        log.info("✅ Token saved to %s", TOKEN_PATH)
        log.info("You can now run:  python linkedin_cert_poster.py --status")
        return

    if args.now:
        success = run_post_job(cfg)
        sys.exit(0 if success else 1)

    # Default: start weekly scheduler
    start_scheduler(cfg)


if __name__ == "__main__":
    main()
# 🎓 LinkedIn Certificate Auto-Poster

Turn a folder of certificate PDFs into a steady stream of LinkedIn posts, automatically.

The tool picks the next certificate from a queue, renders it as an image, asks a **local LLM (Ollama)** to write a short professional post with hashtags, publishes it to your LinkedIn profile through the official API, and moves the PDF to a `posted/` folder. Run it once, or leave it running and it posts every week on a schedule.

> 🔒 Everything except the LinkedIn API call runs on your machine. Your certificates and prompts are never sent to a third-party AI service.

---

## ✨ Features

- **Certificate queue**: drop PDFs into a folder; they are posted one at a time in alphabetical order.
- **Local AI writing**: uses [Ollama](https://ollama.com) to generate a 3–4 sentence post plus 4 hashtags. No API costs.
- **PDF → image**: renders the first page of the certificate with PyMuPDF and auto-compresses it to stay under LinkedIn's 5 MB image limit.
- **Official LinkedIn API**: OAuth 2.0 login, image upload, and UGC post creation.
- **Weekly scheduler**: default is every Thursday at 09:00 (Africa/Cairo); fully configurable.
- **Safe retries**: if anything fails, the certificate stays in the queue and is retried on the next run.
- **Auto token handling**: the access token is stored locally and re-authorisation is triggered when it expires (~60 days).
- **Queue status command**: see what is pending and what has already been posted.
- **Logging**: console and `poster.log` file.

---

## 🧭 How it works

```
 certs/*.pdf
     │
     ▼
 1. Pick next PDF (A → Z)
     │
 2. Extract text + build title from filename
     │
 3. Ollama (local LLM) ──► post text + hashtags
     │
 4. Render page 1 → JPEG (< 5 MB)
     │
 5. LinkedIn API: register upload → upload image → create post
     │
     ▼
 certs/posted/<file>.pdf   (moved only after success)
```

---

## 🎬 Demo

> Add your demo here.

- 📹 Video walkthrough: `<paste YouTube / LinkedIn video link>`
- 🖼️ Screenshots: put them in a `docs/` folder and link them, for example:

```markdown
![Queue status](docs/status.png)
![Generated post](docs/post.png)
```

---

## 📋 Requirements

- Python **3.9+**
- [Ollama](https://ollama.com) installed and running, with at least one model pulled
- A LinkedIn account and a LinkedIn Developer app (setup below)

---

## 🚀 Installation

```bash
git clone https://github.com/<your-username>/<your-repo>.git
cd <your-repo>

python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

pip install pymupdf pillow apscheduler requests pytz
```

Pull a model for Ollama (use any model you like and set its name in `config.json`):

```bash
ollama pull <model-name>
ollama serve
```

---

## 🔑 LinkedIn app setup

1. Go to the [LinkedIn Developer Portal](https://www.linkedin.com/developers/apps) and create an app.
2. In the **Products** tab, request:
   - **Share on LinkedIn** (gives the `w_member_social` scope)
   - **Sign In with LinkedIn using OpenID Connect**
3. In the **Auth** tab, add this redirect URL:
   ```
   http://localhost:8080/callback
   ```
4. Copy your **Client ID** and **Client Secret**. You will paste them into `config.json`.

---

## ⚙️ Configuration

On first run the script creates a `config.json` file and exits so you can fill it in.

| Key | Description | Default |
|---|---|---|
| `linkedin_client_id` | Client ID from your LinkedIn app | – |
| `linkedin_client_secret` | Client Secret from your LinkedIn app | – |
| `linkedin_redirect_uri` | Must match the redirect URL in your app | `http://localhost:8080/callback` |
| `ollama_url` | Ollama server address | `http://localhost:11434` |
| `ollama_model` | Model used to write the post | set to a model you have pulled |
| `schedule_day_of_week` | Day to post | `thu` |
| `schedule_hour` / `schedule_minute` | Time to post | `9` / `0` |
| `timezone` | Scheduler timezone | `Africa/Cairo` |
| `your_name` | Name used in the prompt | – |
| `post_language` | Language setting for the post | `English` |

Folder paths (`CERTS_DIR`, `CONFIG_PATH`, `TOKEN_PATH`, `LOG_PATH`) are constants at the top of `bot.py`. Edit them to match your machine.

---

## ▶️ Usage

```bash
# 1. Log in to LinkedIn once (opens your browser, saves a token)
python bot.py --auth

# 2. See your certificate queue
python bot.py --status

# 3. Publish the next certificate right now (good for testing)
python bot.py --now

# 4. Start the weekly scheduler (keep it running)
python bot.py
```

### Typical workflow

1. Put your certificate PDFs in the `certs/` folder.
2. Run `--auth` once, then `--status` to confirm the queue.
3. Run `--now` to test with a single certificate.
4. Run the scheduler (or put it in `systemd`, `cron`, `tmux`, or Docker) to post automatically.

---

## 📁 Project structure

```
.
├── bot.py                 # the whole tool
├── config.json            # your settings and secrets (git-ignored)
├── linkedin_token.json    # saved access token (git-ignored)
├── poster.log             # run log (git-ignored)
└── certs/
    ├── *.pdf              # pending certificates
    └── posted/            # certificates already published
```

---

## 🛠️ Troubleshooting

| Problem | Fix |
|---|---|
| `Cannot reach Ollama` | Start it with `ollama serve` and check `ollama_url`. |
| Empty response from the model | Try a different or larger model; the script sends `think: false` for reasoning models. |
| `No authorisation code received` | Approve the app in the browser within 120 s and make sure port 8080 is free. |
| `redirect_uri` error | The redirect URL in `config.json` must match your LinkedIn app exactly. |
| `403` when posting | Confirm the **Share on LinkedIn** product is approved for your app. |
| Nothing posts | Run `--status`; the queue may be empty. |

---

## 🔐 Security notes

- **Never commit** `config.json` or `linkedin_token.json`. Add them to `.gitignore`:
  ```
  config.json
  linkedin_token.json
  poster.log
  certs/
  venv/
  ```
- If a Client Secret was ever pasted into a public place, rotate it in the LinkedIn Developer Portal.
- Posts are published as **PUBLIC** immediately. Test with `--now` on a certificate you are happy to share.

---

## 🗺️ Roadmap ideas

- Use the extracted certificate text (issuer, skills) to build richer prompts and tags
- `--dry-run` mode to preview the post before publishing
- Support for image certificates (PNG/JPG) as well as PDFs
- Docker image for easy deployment
- Post to a company page

---

## 👤 Author

**Hosam Ashraf**: feel free to open an issue or pull request.

## 📄 License

Choose a license (MIT is a common choice) and add a `LICENSE` file.

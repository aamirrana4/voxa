# Voxa — AI Voice Studio

Your own text-to-speech & AI audio tool website, powered by the ai33.pro API.
A completely original design — text editor right on the home page, a voice
browser sidebar with **play-before-you-use** sample previews, plus dialogue,
voice cloning, transcription, dubbing, voice changer, voice isolate, music,
sound effects and image generation.

## How it works

```
Browser  →  Voxa backend (this code)  →  https://api.ai33.pro
                ↑
     API key lives ONLY here (env var)
```

The API key is **never** in the frontend code. The browser only talks to your
own server; the server adds the key and forwards requests to ai33.pro.

## Run locally

```bash
cd voxa/backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# put your key in the environment (never commit it)
export AI33_API_KEY="your-key-here"
python3 app.py
```

Open http://localhost:5000

## Deploy (so it works with your domain later)

Any Python host works (VPS, Render, Railway, Hetzner…). Example with gunicorn:

```bash
pip install -r requirements.txt
export AI33_API_KEY="your-key-here"
gunicorn app:app --bind 0.0.0.0:5000 --workers 2 --timeout 600
```

**When you give me the domain**, I will:
1. Point the domain's DNS (A record) to your server IP, **or** add it as a
   custom domain in Render/Railway/your host,
2. Enable HTTPS (Let's Encrypt / host-provided certificate),
3. Set `AI33_API_KEY` in the host's environment variables,
4. Verify every tool end-to-end on the live domain.

### Recommended hosts
- **Render.com** — free tier, set env var in dashboard, add custom domain in 2 clicks.
- **Railway.app** — similar, good for beginners.
- **VPS (Hetzner/Contabo)** — cheapest long-term; run gunicorn + nginx + certbot.

## Project layout

```
voxa/
├── backend/
│   ├── app.py            # Flask server: serves frontend + /api/* proxy
│   └── requirements.txt
├── frontend/
│   ├── index.html        # all tool pages
│   ├── styles.css        # warm light theme
│   └── app.js            # voice browser, players, generation flows
├── .env.example          # copy to .env locally (git-ignored pattern)
└── README.md
```

## Features

| Tool | What it does |
|---|---|
| 🎙️ Speak | Text → speech, 7 voice providers, speed control, SRT transcript |
| 💬 Dialogue | Multi-speaker (A/B/C…) with per-speaker voices + previews |
| 🧬 Clone Voice | Clone from a 3–30s sample (≤10MB) |
| 📝 Transcribe | Audio → text + SRT + word timings |
| 🌍 Dubbing | Translate audio into 12 languages |
| 🔁 Voice Changer | Speech → another voice, stability/similarity controls |
| 🎧 Isolate Voice | Strip background noise |
| 🎵 Music | AI songs from a description |
| ✨ Sound FX | Custom sound effects |
| 🖼️ Images | Thumbnails & visual assets |
| 🕘 History | All past tasks, re-download, delete |

## Pricing (PKR)

The site has a built-in **💰 Pricing** page with three plans:

| Plan | Price | Credits |
|---|---|---|
| Starter | Rs 800/month | 500,000 (5 lakh) |
| Creator | Rs 1,600/month | 1,000,000 (10 lakh) |
| Lifetime | Rs 2,000 one-time | 1,000,000 (10 lakh), never expire |

Each plan's **Buy on WhatsApp** button opens a chat with a pre-written message.
Set your number once in `frontend/app.js`:

```js
const SUPPORT_WHATSAPP = "923001234567"; // apna number yahan
```

Payment flow is manual: customer pays via JazzCash/Easypaisa/bank transfer,
shares the screenshot on WhatsApp, and you activate their credits. (Automated
per-user credit limits would need a login/accounts system — a bigger build;
ask when you're ready for it.)

## Costs to keep in mind

Every generation spends **your** ai33 credits (check the 🪙 pill in the header).
If you sell access, price above your per-generation credit cost.

## Rebranding

Search the `frontend/` folder for "Voxa" to rename the site, and tweak colors
in `frontend/styles.css` (`:root` variables at the top).

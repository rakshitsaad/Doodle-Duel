# ✏️ Doodle Duel

**Pictionary against a robot with opinions.** You draw, and **Gemma 4** watches your sketch and guesses what it is, live, with a confidence meter and a running commentary. Or flip it around: Gemma hands you a word, you draw it, and Gemma's art-critic mode scores your masterpiece out of 10.

Two files, no build step, no database. Open source and made for Hacktoberfest 2026.

---

## 🎮 Game modes

### 1. You draw, Gemma guesses
- You get a secret word and **60 seconds**.
- Every time you pause for **1.5 s**, the canvas goes to Gemma 4. It replies with a guess, a confidence score and a short reaction.
- Gemma's guesses show up in a speech bubble from a robot avatar, with a typing animation, a confidence bar and a scrolling guess log.
- Close guesses count: `cats` matches `cat`, `ice-cream` matches `ice cream`, small typos are fine, and some words have aliases (`bike` → `bicycle`).
- When Gemma gets it: confetti, "Gemma got it in 12s!", and points (faster = more).

### 2. Gemma challenges you
- Gemma picks a word, you draw it and press **Done**.
- Gemma scores it **0–10** on an animated gauge and writes a one- or two-sentence critique.

### Also included
- Pen sizes, 5 colours, eraser, undo (Ctrl+Z, which also undoes Clear), clear
- Mouse, touch and stylus support through Pointer Events, drawn as smooth curves
- Scoreboard with points, Gemma's hit rate, fastest guess, streaks and average art score, saved in `localStorage`
- Works on phones, tablets and desktops, and respects `prefers-reduced-motion`

---

## 🧠 How Gemma 4 is used

Doodle Duel runs Gemma 4's **multimodal (image + text) understanding** through the [Gemini API](https://ai.google.dev/) with the official `google-genai` Python SDK.

| Step | What happens |
|---|---|
| 1 | The browser redraws your strokes onto a clean 640×480 white canvas and sends it as a base64 PNG. |
| 2 | `main.py` decodes and checks the image, then sends it to Gemma 4 as an inline image part together with a prompt. |
| 3 | The request sets `response_mime_type="application/json"` so Gemma replies with structured JSON. |
| 4 | The reply is parsed defensively (code fences, extra chatter, `0.8` vs `80` confidence) and clamped to safe values. |

**Models**
- Primary: `gemma-4-26b-a4b-it`, a fast Mixture-of-Experts model with about 4B active parameters, well suited to live guessing.
- Fallback: `gemma-4-31b-it`, the larger dense model, used automatically if the primary call fails.
- If a model rejects JSON mode, the server retries the same model in plain-text mode before falling back.

**Prompts**
- `/guess`: *"You are a playful Pictionary player watching someone draw. Look at this (possibly unfinished) sketch…"* The prompt includes Gemma's previous guesses, so it moves on from wrong answers instead of repeating them.
- `/judge`: Gemma acts as a witty but kind art critic, scoring how recognisable the drawing is as the target word. Writing the word as text instead of drawing it gets called out.

Every failure path (missing key, bad key, rate limit, timeout, unparseable reply, bad image) returns a friendly JSON message. The server doesn't crash and the UI doesn't freeze.

---

## 🚀 Run locally

**Requirements:** Python 3.10+ and a Gemini API key from [Google AI Studio](https://aistudio.google.com/apikey).

### Windows (PowerShell)
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:GEMINI_API_KEY = "your-key-here"
uvicorn main:app --reload --port 8080
```

### macOS / Linux
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export GEMINI_API_KEY="your-key-here"
uvicorn main:app --reload --port 8080
```

Then open **http://localhost:8080** and start drawing.

> `python main.py` also starts the server on port 8080 (or `$PORT`).
>
> **Tip:** instead of exporting the key every time, put `GEMINI_API_KEY=your-key-here` in a `.env` file next to `main.py`. It is loaded automatically and git ignores it.

### Configuration

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | *(required)* | Your Gemini API key |
| `GEMMA_MODEL` | `gemma-4-26b-a4b-it` | Primary model |
| `GEMMA_FALLBACK_MODEL` | `gemma-4-31b-it` | Model used if the primary call errors |
| `PORT` | `8080` | Port used by the Procfile / `python main.py` |

### API

| Method | Path | Body | Returns |
|---|---|---|---|
| `GET` | `/` | | The game |
| `GET` | `/health` | | `{ok, models, key_configured}` |
| `GET` | `/challenge?avoid=cat` | | `{ok, word, difficulty, aliases}` |
| `POST` | `/guess` | `{image, history: []}` | `{ok, guess, confidence, comment, model}` |
| `POST` | `/judge` | `{image, word}` | `{ok, score, critique, model}` |

On failure, endpoints return `{ok: false, error: "friendly message"}`.

---

## ☁️ Deploy on DigitalOcean App Platform

The repo is ready for App Platform's Python buildpack: `requirements.txt` handles dependencies and the `Procfile` sets the run command.

1. **Push to GitHub.** Fork or push this repo to your GitHub account.
2. **Create the app.** In the DigitalOcean control panel go to **Apps → Create App → GitHub**, then pick the repo and the `main` branch. Leave *Autodeploy* on if you want each push to redeploy.
3. **Check the detected component.** App Platform should detect a **Python** web service.
   - Build command: *(leave empty)*
   - Run command: `uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}` (taken from the `Procfile`)
   - HTTP port: `8080`
4. **Add the secret.** Under **Environment Variables**, add `GEMINI_API_KEY`, paste your key, and tick **Encrypt**.
5. **Pick a size.** The smallest Basic instance is enough, since the heavy lifting happens in the Gemini API.
6. **Deploy.** Click **Create Resources**. When the build finishes you get a `*.ondigitalocean.app` URL with HTTPS.

<details>
<summary>Prefer an app spec? (<code>doctl apps create --spec app.yaml</code>)</summary>

```yaml
name: doodle-duel
services:
  - name: web
    github:
      repo: <your-github-user>/doodle-duel
      branch: main
      deploy_on_push: true
    environment_slug: python
    run_command: uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}
    http_port: 8080
    instance_size_slug: basic-xxs
    instance_count: 1
    health_check:
      http_path: /health
    envs:
      - key: GEMINI_API_KEY
        scope: RUN_TIME
        type: SECRET
        value: your-key-here
```
</details>

---

## 🗂️ Project structure

```
.
├── main.py            # FastAPI app: routes, Gemma 4 calls, JSON parsing, word list
├── static/
│   └── index.html     # The whole game: HTML + CSS + vanilla JS, no build step
├── requirements.txt
├── Procfile
├── LICENSE            # MIT
└── README.md
```

---

## 🤝 Contributing

PRs are welcome, especially during **Hacktoberfest**. Fork the repo, make a branch, keep it to the two-file spirit where you can, and open a PR with a screenshot or GIF.

**Ideas, from small to big:**

- 🟢 **More words:** grow the `WORDS` list in `main.py`, with aliases and difficulty levels
- 🟢 **Translations:** localise the UI copy and prompts
- 🟢 **Sound effects:** pencil scratch, robot beeps and a victory fanfare, with a mute toggle
- 🟢 **Dark mode:** a chalkboard theme
- 🟡 **Hard mode:** abstract words ("freedom", "Monday") and a 30-second timer
- 🟡 **Share your doodle:** export the drawing plus Gemma's verdict as a PNG card
- 🟡 **Hint system:** Gemma asks a yes/no question when it's stuck
- 🟡 **Accessibility:** keyboard drawing, better screen-reader narration of guesses
- 🟡 **Streaming guesses:** stream Gemma's comment token by token instead of simulating typing
- 🔴 **Multiplayer:** humans and Gemma race to guess the same drawing over WebSockets
- 🔴 **Daily challenge:** the same word for everyone each day, with a global leaderboard
- 🔴 **Replay:** record strokes and replay how the drawing evolved next to Gemma's guesses
- 🔴 **Tests:** pytest coverage for JSON parsing, image validation and the fallback logic

Please keep the code friendly to beginners: readable, commented where it isn't obvious, and with no build tooling.

---

## 📄 License

[MIT](LICENSE). Draw freely.

<p align="center"><b>Open source · Powered by Gemma 4 · Hacktoberfest 2026</b></p>

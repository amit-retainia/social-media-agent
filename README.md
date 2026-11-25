# Social Media Agents - Python project

A team of AI agents runs social media for one small business.
You talk only to the **Manager**. The Manager runs the other agents.

```
1 Interviewer -> 2 Planner -> 3 Creator -> 4 Owner approval -> 5 Publisher -> 6 Analyst
                     ^                                                           |
                     +---------------- learnings (better next posts) ------------+
```

All agents read and write the **Business space** (one per business):
brand brief, connected accounts, content calendar, content plan, media library, learnings.

**Your only work: add your API keys to `.env`.**
Every outside service switches on when its key is in `.env`.
When a key is missing, that service is simulated, so the app still runs end to end.

## Keys you need

| Service | What it does | Key in `.env` | Where to get it |
|---|---|---|---|
| Claude (Anthropic) | Text for all agents. Planner trends with web search. | `ANTHROPIC_API_KEY` | https://console.anthropic.com |
| **MachGen** | Creator makes images (FLUX.2-dev) **and** video clips (Wan2.2-A14B). One key for both. | `MACHGEN_API_KEY` | https://www.machgen.ai -> Profile -> API Keys |
| ElevenLabs | Creator makes the voiceover (its timestamps time the captions and scene cuts) | `ELEVENLABS_API_KEY` (+ `ELEVENLABS_VOICE_ID`) | https://elevenlabs.io |
| OpenAI images (optional) | Other image option (gpt-image-2), used only with no MachGen key or `IMAGE_PROVIDER=openai` | `OPENAI_API_KEY` | https://platform.openai.com/api-keys |
| Runway (optional) | Other video option (gen4.5), used only with no MachGen key or `VIDEO_PROVIDER=runway` | `RUNWAYML_API_SECRET` | https://dev.runwayml.com |
| Ayrshare | Connect accounts, post, comments and replies, metrics, on Instagram, Facebook, X, LinkedIn, TikTok and YouTube | `AYRSHARE_API_KEY` (+ optional `AYRSHARE_PROFILE_KEY`) | https://app.ayrshare.com |
| Amazon S3 (optional) | Public links for media. Without it, media goes to Ayrshare storage. | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, `S3_BUCKET` (+ optional `S3_PUBLIC_BASE_URL`) | AWS console |
| **Telegram bot** (Owner app on your phone) | Step 4: each draft comes to your phone with **Approve** / **Ask for changes** buttons. Alerts come here too. Without it, you review in the terminal. | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Telegram -> @BotFather -> /newbot (free) |
| Webhook alerts (optional) | Alerts to Slack, Discord or Teams | `ALERT_WEBHOOK_URL` | Your chat app's "incoming webhook" |
| Email alerts (optional) | Alerts by email | `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `ALERT_EMAIL_FROM`, `ALERT_EMAIL_TO` | Your email provider |
| Video editing, captions, sizes | Joins clips, burns in captions, adds the voiceover, sizes every file per platform | no key (ffmpeg from `imageio-ffmpeg`, Pillow) | - |
| Website reader | Interviewer reads your website | no key | - |

`.env.example` lists every key with notes. See which services are real:

```bash
python main.py --check
```

Notes:
- **Ayrshare:** link each social account once in the Ayrshare dashboard (Social Accounts).
  Ayrshare keeps the logins (OAuth tokens), so no tokens are saved in this project.
  For many businesses, use one Ayrshare profile per business:
  `AYRSHARE_PROFILE_KEY_<BUSINESS>` (capitals, `-` becomes `_`), e.g. `AYRSHARE_PROFILE_KEY_SWEET_CRUMBS`.
- **MachGen** is the default for images and video. With only `MACHGEN_API_KEY` and `ELEVENLABS_API_KEY`
  the Creator makes real, postable photos, carousels, reels and videos.
  Change models with `MACHGEN_IMAGE_MODEL` / `MACHGEN_VIDEO_MODEL` (list: https://www.machgen.ai/docs/models).
  The app reads each model's allowed sizes and lengths from MachGen, so any listed model works.
- **Save money on video:** `AI_VIDEO_ENABLED=0` makes reels as a slideshow of AI images with a slow zoom
  (still real and postable). OpenAI's Sora video API was shut down in September 2026.
- **If a service fails** (for example a 500 error), the Creator keeps going with a placeholder file
  and adds a warning to the draft. The Publisher does not post placeholder media.

## What each agent does

| Agent | Works now |
|---|---|
| Manager | Chats with you, routes work to each agent, updates the brand brief |
| 1. Interviewer | Asks questions, reads your website, checks linked accounts, writes the brand brief (with time zone and closed days) |
| 2. Planner | Weekly content calendar and news posts. Picks topics, formats (post, carousel, reel, video), days and times. Writes a script per post. Uses trends and the Analyst's learnings. Saves `content_plan.json` |
| 3. Creator | Final copy, images (MachGen), video clips (MachGen), voiceover (ElevenLabs), edit with captions (ffmpeg), sizes per platform (9:16, 1:1, 16:9), text per platform |
| 4. Owner approval | Preview on your phone (Telegram) or in the terminal; approve, or ask for changes with a short note; the Creator fixes and sends it again; reminders for slow approvals |
| 5. Publisher | Posts at the set time, 3 retries, alerts, new time from the Planner, replies to comments |
| 6. Analyst | Metrics at 1 h, 24 h, 7 days; finds what works; learnings go to the Planner |

## The Planner (step 2)

1. Reads the brand brief, trends (web search) and learnings from the Analyst.
2. Picks topics and formats: **post, carousel, reel, video**.
3. Sets dates and times: one post per platform per day, at good local times, in the business time zone.
   It skips closed days and blackout dates from the brand brief.
4. Sends the schedule to the content calendar.
5. Writes a script per post:
   - **reel / video:** `hook`, `script`, `caption`, `hashtags`.
     `script` is a list of shots in order. Each shot has `show` (what the camera shows), `say` (the words), `seconds`.
   - **post / carousel:** `copy_idea`, `caption`, `hashtags` (carousel also has `slides`).

Two ways to use it (the Manager picks the right one):
- **"Plan my week"** -> a weekly calendar. The number of posts is `posts_per_week` from the brand brief.
  Example for 3 posts: Monday photo post, Wednesday baking reel, Friday carousel.
- **News**, e.g. "I baked a new chocolate cake, I want people to know" -> one post on the **next suitable day**.
  Then the Creator makes the draft, and you approve it.

Learning loop: when the Analyst finds "Best format: reel" or "Best hour: 18", the Planner uses more reels and that hour.

The plan is saved in `data/businesses/<business>/content_plan.json`:
upcoming posts by week and day, each with its script, plus `ready_for_creator` (the post IDs for the Creator).
Each script is also saved in `media_library/<post id>/script.json`.

## The Creator (step 3)

It follows the plan's steps for every planned post (and every post you asked to change):

1. **Gets the script** from the Planner.
2. **Text:** writes the final copy in the brand voice (Claude), plus a text for each platform
   inside its limit (X: 280 characters, 2 hashtags; Instagram: 2,200, up to 15 hashtags; and so on).
3. **Image (post, carousel):** writes an image prompt (1 for a post, 3-5 for a carousel) and makes it with
   the AI image model (MachGen). Your own photos come first: put them in
   `data/businesses/<business>/media_library/<post id>/owner/` and the Creator uses them (no AI cost).
4. **Video (reel, video):** one scene per shot of the script.
   - Voiceover: ElevenLabs reads the whole script once. Its timestamps set when each scene starts and ends.
   - Clips: MachGen makes one clip per scene (text to video, or `VIDEO_MODE=image`: image first, then animate).
   - Edit: ffmpeg trims or holds each clip to its voice line, joins them and adds the voiceover.
5. **Captions and sizes:** short caption lines (a few words, timed to the voice) are burned in, and a `.srt`
   file is saved. Every file is made in the exact size for each platform:
   9:16 = 1080x1920, 1:1 = 1080x1080, 16:9 = 1920x1080. Clips are made once per shape (9:16 or 16:9);
   1:1 is cut from 9:16, so you do not pay twice.
6. **Media library:** everything goes to `media_library/<post id>/`: `script.json`, `draft_v<N>.json`,
   raw images, clips, voiceover, captions, and the final files `v<N>_<size>_...`
   (for example `v1_9x16_final.mp4`, `v1_1x1_image1.jpg`).
7. **Owner approval:** the draft gets status `pending_approval` (waiting for approval) and you get an alert.

No key for a service? That part is **simulated**: files end in `.sim.mp4`, `.svg` and so on, the preview says
SIMULATED, and the Publisher never posts them. If a service fails, the draft gets a warning and a placeholder.

## Owner approval (step 4)

It follows the plan's steps for every draft:

1. **Preview in the Owner app.** With Telegram on, the Creator sends each draft to your phone:
   the image (or album for a carousel, or the video) and the text (copy, caption, hashtags, platforms, time).
   With Telegram off, the preview shows in the terminal.
2. **Approve** -> status `approved`. The post is in the Publisher's queue and goes out at its time.
3. **Ask for changes** -> the bot asks "What should change?". Send a short note (for example "say it is eggless").
   The post gets status `changes_requested` and goes back to the Creator with your note.
4. **The Creator fixes it and sends it again**: a new preview (version 2, 3, ...) with new buttons.
   Old buttons stop working, so you never approve an old version.

On the phone you can also **reply** to a preview with `yes`, or with your change note.
In the terminal, type `yes`, `later`, or a short note on what to change.

Slow approvals (plan "Risks"): if a draft is not approved `APPROVAL_REMINDER_HOURS` (2) before its time,
you get a reminder. If the time passes, the post moves to the next free slot and you are told.
Only your chat ID can approve. Messages from other people are ignored.

### Set up Telegram (5 minutes, free)

1. In Telegram, open **@BotFather**. Send `/newbot`. Pick a name. Copy the token into `TELEGRAM_BOT_TOKEN`.
2. Open your new bot (the link BotFather gives you) and send `hi`.
3. Run `python main.py --telegram-setup`. It checks the token and prints your chat ID.
   Put it in `TELEGRAM_CHAT_ID`. Run it again: you get a test message on your phone.
4. Start the always-on runner: `python main.py --bot` (see below).

More businesses (one bot, many owners): `TELEGRAM_CHAT_ID_<BUSINESS>`, e.g. `TELEGRAM_CHAT_ID_SWEET_CRUMBS`.

### Keep it running: `python main.py --bot`

The chat waits `APPROVAL_WAIT_SECONDS` (300) for your answers. To answer at any time, keep this running:

```bash
python main.py --bot
```

It listens to your phone, and every `BOT_PUBLISH_EVERY_MINUTES` (5) it, for every business:
sends new previews, sends reminders, and runs the Publisher (posts what is due).
Run it on a computer or server that is always on (for example with `nohup`, `systemd`, `pm2` or a small cloud VM).
With `--bot` running you do not need the cron job for `--publish`.

### WhatsApp instead of Telegram (later)

Telegram is free and needs no business checks, so it is the default.
For WhatsApp, use the WhatsApp Business Cloud API (Meta) or Twilio. You need a business number,
and approved message templates for messages you start. Add `tools/whatsapp.py` with the same functions as
`tools/telegram.py` (send text with 2 reply buttons, send image/video, read replies by webhook),
and call `owner_approval.approve()` / `request_changes()` from it.

## 1. What you need

- Python **3.10 or newer**. Check it: `python3 --version`
- Your API keys (see the table above). No keys yet? It still runs in mock mode.

## 2. Unzip and open the folder

```bash
cd social-media-agent
```

## 3. Make a virtual environment

macOS / Linux:
```bash
python3 -m venv .venv
source .venv/bin/activate
```

Windows (PowerShell):
```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
```

You see `(.venv)` at the start of the line. Good.

## 4. Install the packages

```bash
pip install -r requirements.txt
```

## 5. Add your keys

```bash
cp .env.example .env          # Windows: copy .env.example .env
```

Open `.env`. Put each key after its `=`. Set `TIMEZONE` (for example `Asia/Kolkata`). Save the file.
Never share this file. Never put it in git.
Then check: `python main.py --check`

## 6. Run it

```bash
python main.py --business sweet-crumbs
```

Try this:
1. Answer the Interviewer's questions.
2. Type: `plan my week` -> see the week. Type `make drafts`, then approve each one.
3. Type: `I baked a new chocolate cake, I want people to know`
4. Look at the preview (on your phone with Telegram, or here). Approve, or send a short note on what to change.
5. Type: `show the plan`
6. Type: `post it now` (or wait for the planned time).
7. Type: `how did my posts do?`
8. Type `quit` to stop.

More options:

```bash
python main.py --mock                 # mock mode (no Claude), even with a key
python main.py --publish              # Publisher only: post what is due, then stop
python main.py --check                # which services are real or simulated
python main.py --bot                  # always on: phone approvals, reminders, Publisher on time
python main.py --telegram-setup       # check the Telegram bot and find your chat ID
```

Run `python main.py --bot` (see Owner approval), or run `python main.py --publish --business sweet-crumbs`
every 5 minutes (cron or Windows Task Scheduler), so posts go out on time when the chat is closed.

## 7. See the results

Everything is saved in `data/businesses/<business name>/`:

- `brand_brief.json` - what the Interviewer learned
- `connected_accounts.json` - linked apps
- `content_calendar.json` - every post, its time, script, draft and status
- `content_plan.json` - the Planner's plan, ready for the Creator
- `media_library/<post id>/` - script, drafts, images, clips, voiceover, final videos
- `learnings.json` - what works best (the Planner reads it)
- `alerts.json` - messages for you (failed posts, hard comments, approval reminders)
- `data/businesses/_telegram_state.json` - the bot's memory (which button belongs to which draft)

## 8. Run the tests

```bash
python -m unittest -v
```

The tests need no keys. They check the full loop, the Planner, the Creator, and every real integration
against a fake server (MachGen, OpenAI, Runway, ElevenLabs, Ayrshare, Telegram, webhook, web search),
plus real ffmpeg edits with captions and exact sizes per platform.

## Settings (in .env)

| Name | Default | What it does |
|---|---|---|
| `CLAUDE_MODEL` | `claude-sonnet-5-5` | The Claude model |
| `TRENDS_WEB_SEARCH` | `1` | Planner searches the web for trends |
| `MACHGEN_IMAGE_MODEL` | `FLUX.2-dev` | MachGen image model |
| `MACHGEN_VIDEO_MODEL` | `Wan2.2-A14B` | MachGen video model |
| `MACHGEN_VIDEO_HEIGHT` | `720` | Video height (nearest allowed height is used) |
| `IMAGE_PROVIDER` / `VIDEO_PROVIDER` | `auto` | `auto` = MachGen if its key is set. Or `openai` / `runway` |
| `VIDEO_MODE` | `text` | `text` = text to video. `image` = AI image first, then animate it |
| `AI_VIDEO_ENABLED` | `1` | `0` = no AI video clips |
| `SLIDESHOW_FALLBACK` | `1` | No AI video: make reels from AI images with a slow zoom |
| `BURN_IN_CAPTIONS` | `1` | Burn captions into videos (`.srt` is always saved) |
| `CAPTION_FONT` | empty | Path to a `.ttf` brand font for captions |
| `ELEVENLABS_VOICE_ID` | Rachel | The voice for voiceovers |
| `OPENAI_IMAGE_QUALITY` | `medium` | OpenAI image quality (only if you use OpenAI) |
| `RUNWAY_VIDEO_MODEL` | `gen4.5` | Runway model (only if you use Runway) |
| `APPROVAL_WAIT_SECONDS` | `300` | How long the chat waits for your answers on the phone |
| `APPROVAL_REMINDER_HOURS` | `2` | Reminder before the post time if a draft is not approved |
| `BOT_PUBLISH_EVERY_MINUTES` | `5` | `--bot`: minutes between Publisher runs |
| `TIMEZONE` | `UTC` | Default time zone for posting times |
| `PLANNER_MIN_LEAD_HOURS` | `3` | Time between planning and the first possible post |
| `PUBLISH_RETRY_WAIT` | `2` | Seconds between Publisher retries |
| `SIMULATE_FAIL_RATE` | `0.0` | Test failures (simulated platforms): `1.0` = every post fails |

## Project files

```
main.py                     command-line chat with the Manager (--check, --publish, --mock, --bot, --telegram-setup)
social_agent/
  config.py                 settings and keys; services() shows real vs simulated
  llm.py                    all Claude calls (Anthropic Python SDK)
  business_space.py         the shared Business space (JSON files today)
  owner_app.py              the Owner app in the terminal
  bot.py                    always-on runner: phone answers, reminders, Publisher on time
  agents/
    manager.py              Manager agent - routes work with tools
    interviewer.py          1. Interviewer
    planner.py              2. Planner - weekly calendar, news posts, scripts, content_plan.json
    creator.py              3. Creator
    owner_approval.py       4. Owner approval - phone (Telegram) or terminal; approve, changes, reminders
    publisher.py            5. Publisher
    analyst.py              6. Analyst
  tools/
    website.py              reads the business website
    trends.py               trends: Claude web search (or a fixed list)
    ai_models.py            provider layer: images, video clips, voice (picks MachGen / OpenAI / Runway / ElevenLabs)
    machgen.py              MachGen REST API: images and video clips
    media_edit.py           sizes per platform, captions, video edit (Pillow + ffmpeg)
    platforms.py            social platforms through Ayrshare (or simulated)
    storage.py              media links: S3 or Ayrshare storage
    telegram.py             Telegram Bot API: previews, buttons, replies (the Owner app on the phone)
    notify.py               owner alerts: Telegram, webhook and email
    http.py                 shared HTTP helper
tests/                      test_flow.py, test_planner.py, test_creator.py, test_owner_approval.py, test_integrations.py
```

## Still to do (from the plan)

- Owner app (web and mobile) with the same approve / change buttons. The phone works today through Telegram.
- Postgres, a vector store and a job queue instead of JSON files and cron.
- Direct platform APIs instead of Ayrshare, if you need them later.

## Problems?

- `ModuleNotFoundError`: turn on the venv (step 3), then do step 4 again.
- `AuthenticationError`: your Anthropic key in `.env` is wrong.
- `model not found`: set `CLAUDE_MODEL` in `.env` to a model your account can use.
- An account shows `needs_login`: link it in the Ayrshare dashboard, then say `connect my accounts again`.
- Telegram: no preview on the phone -> run `python main.py --telegram-setup`. Check `TELEGRAM_CHAT_ID`.
  `Conflict: terminated by other getUpdates request` -> only run one `--bot` per bot token.
  A video bigger than 50 MB is not shown on the phone; the text and buttons still come.
- `python main.py --check` shows SIMULATED for a service: its key is missing or empty in `.env`.
- A draft has a MachGen warning with `HTTP 400`: the model does not take that size or length.
  Try another `MACHGEN_VIDEO_MODEL` / `MACHGEN_IMAGE_MODEL`, or check your MachGen balance.
- `HTTP 429` from MachGen: too many tasks at once. The app waits and tries again 3 times.

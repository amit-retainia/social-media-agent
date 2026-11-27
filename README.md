# Social Media Agents - Python project

A team of AI agents runs social media for one business.
The owner talks only to the **Manager**: an AI agent such as Grok Bot (skill `skills/social-manager`).
The Manager writes all text (plans, scripts, captions, replies, learnings) and runs the team's steps with
`python main.py --tool <step>`. This app has **no AI text model inside**: a step that needs text answers
`needs_text` with the rules and the data, and the agent runs it again with the text.

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

**Grok Bot:** see [GROK.md](GROK.md) (setup, keys, routine, skill).

## Keys you need

| Service | What it does | Key in `.env` | Where to get it |
|---|---|---|---|
| **MachGen** | Creator makes images (Nano-Banana-2) **and** video clips (Vidu-Q3-Turbo). One key for both. | `MACHGEN_API_KEY` | https://www.machgen.ai -> Profile -> API Keys |
| Fish Audio | Creator makes the voiceover (model s2.1-pro-free, no cost) | `FISH_API_KEY` (+ `FISH_VOICE_ID`) | https://fish.audio |
| Groq (free tier) | Whisper word times for the captions and scene cuts. Without it, times are estimated | `GROQ_API_KEY` | https://console.groq.com/keys |
| OpenAI images (optional) | Other image option (gpt-image-2), used only with no MachGen key or `IMAGE_PROVIDER=openai` | `OPENAI_API_KEY` | https://platform.openai.com/api-keys |
| Runway (optional) | Other video option (gen4.5), used only with no MachGen key or `VIDEO_PROVIDER=runway` | `RUNWAYML_API_SECRET` | https://dev.runwayml.com |
| Instagram | Post, comments and replies, metrics (Meta Graph API) | `INSTAGRAM_ACCESS_TOKEN`, `INSTAGRAM_USER_ID` | Meta app with "Instagram API with Instagram Login" |
| Facebook Page | Post, comments and replies, metrics (Meta Graph API) | `FACEBOOK_PAGE_ID`, `FACEBOOK_PAGE_TOKEN` | Meta app, Page access token |
| X (Twitter) | Post, metrics (X API v2; replies are not read) | `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_SECRET` | developer.x.com (paid API) |
| LinkedIn | Post, comments and replies, likes (Posts API), profile or company page | `LINKEDIN_ACCESS_TOKEN`, `LINKEDIN_AUTHOR_URN` | LinkedIn developer app |
| Discord | Draft previews and alerts (owners approve in the Grok Bot chat) | `DISCORD_WEBHOOK_URL` | Discord channel -> Integrations -> Webhooks |
| Cloudflare R2 (needed to post) | Public links for media: Instagram and Facebook download from them. | `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET` (+ optional `R2_PUBLIC_BASE_URL`) | Cloudflare dashboard -> R2 |
| Telegram bot (optional, normal computer only) | Step 4: each draft comes to your phone with **Approve** / **Ask for changes** buttons. Alerts come here too. Without it, you review in the terminal. | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Telegram -> @BotFather -> /newbot (free) |
| Webhook alerts (optional) | Alerts to Slack or Teams | `ALERT_WEBHOOK_URL` | Your chat app's "incoming webhook" |
| Email alerts (optional) | Alerts by email | `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `ALERT_EMAIL_FROM`, `ALERT_EMAIL_TO` | Your email provider |
| Video editing, captions, sizes | Joins clips, burns in captions, adds the voiceover, sizes every file per platform | no key (ffmpeg from `imageio-ffmpeg`, Pillow) | - |
| Website reader | Interviewer reads your website | no key | - |

`.env.example` lists every key with notes. See which services are real:

```bash
python main.py --check
```

Notes:
- **Instagram, Facebook, X and LinkedIn** (TikTok and YouTube are not supported yet).
  For many businesses, add `_<BUSINESS>` to each Meta key (capitals, `-` becomes `_`),
  e.g. `INSTAGRAM_USER_ID_SWEET_CRUMBS`.
- **MachGen** is the default for images and video. With only `MACHGEN_API_KEY`, `FISH_API_KEY` and `GROQ_API_KEY`
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
| 2. Planner | Weekly content calendar and news posts. Picks topics, formats (post, carousel, reel, video), days and times. Writes a script per post. Uses web trends (the agent searches) and the Analyst's learnings. Saves `content_plan.json` |
| 3. Creator | Final copy, images (MachGen), video clips (MachGen), voiceover (Fish Audio), edit with captions (ffmpeg), sizes per platform (9:16, 1:1, 16:9), text per platform |
| 4. Owner approval | Preview on your phone (Telegram) or in the terminal; approve, or ask for changes with a short note; the Creator fixes and sends it again; reminders for slow approvals |
| 5. Publisher | Posts at the set time, 3 retries, alerts, new time from the Planner, replies to comments |
| 6. Analyst | Saves each post's numbers at 1 h, 24 h, 3 days, 7 days (on time, by the bot) and the follower count every day; scores posts by growth (follows, shares, saves); finds what works; learnings go to the Planner; weekly report to you |

## The Analyst (step 6)

- **Counter** (the bot, every round): saves each post's numbers when it is 1 h, 24 h, 3 days and 7 days old
  (reach, views, likes, comments, shares, saves, follows, profile visits), and each account's follower count once a day.
  Numbers taken late (the bot was off) are saved but marked `late` and not compared.
- **Growth score** = (follows x 3 + shares x 2 + saves) per 1,000 reach. These actions bring new followers.
  A platform that gives none of them: (likes + comments) per 1,000 reach.
- **Fair comparisons:** same platform, same post age, reach of 100 or more, and the same numbers for every post.
  A "best" format or hour needs 3 posts in each of 2 groups and a 10% lead. Before that it says "too early".
- **Weekly report** (Monday 9:00 local time, to your phone): follower change, posts this week, top post, the running test.

### Tests: the usual way vs one new idea (`experiments.py`)

- **One test at a time.** A test changes ONE thing: a format, a posting hour, or an idea (topic, hook, caption style).
- **Queue:** ideas wait in a queue. From you or the agent (`add_test`: trends, other accounts' strong posts), or
  from the Analyst (formats and hours not tried lately, added when the queue is empty). Your ideas go first.
  Ideas from trends that wait more than 6 weeks are dropped.
- **Your OK:** content ideas from the agent or the Scout (a trend, another shop's style) are sent to you first:
  "Try it? Yes / No" (Telegram buttons, or the chat). Yes = it goes to the queue. No = not asked again for 8 weeks.
  No answer in 3 days = skipped. Formats, hours and your own ideas need no OK.
- **Planner:** about 30% of each week's posts are test posts (`TEST_SHARE`). The other posts use what works.
- **Decision** (the bot), when the 2 test posts have their 3-day numbers, by growth score vs the usual posts:
  20% better and both test posts better = **won** (it becomes the usual way: "Best format: carousel");
  20% worse and both worse = **lost** (not tried again for 8 weeks); a small gap, or the 2 posts do not agree =
  1 more test post, then **no difference** (keep the usual way). You get the result. Then the next idea starts.
  At 3 posts a week (1 test post a week), a test takes about 10 days, or 17 days when it is unclear.

## The Planner (step 2)

1. Reads the brand brief and learnings from the Analyst; the agent searches the web for trends.
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
2. **Text:** the agent writes the final copy in the brand voice, plus a text for each platform
   inside its limit (X: 280 characters, 2 hashtags; Instagram: 2,200, up to 15 hashtags; and so on).
3. **Image (post, carousel):** writes an image prompt (1 for a post, 3-5 for a carousel) and makes it with
   the AI image model (MachGen). Your own photos come first: put them in
   `data/businesses/<business>/media_library/<post id>/owner/` and the Creator uses them (no AI cost).
4. **Video (reel, video):** one scene per shot of the script.
   - Voiceover: Fish Audio reads the whole script once. Whisper on Groq times each word; the times set when each scene starts and ends.
   - Clips: MachGen draws a start image per scene (Nano-Banana-2), then animates it (Vidu-Q3-Turbo). `VIDEO_MODE=text` = text to video.
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
sends new previews, sends reminders, runs the Publisher (posts what is due), and saves the Analyst's numbers
(post numbers on time, follower count, weekly report).
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
- Your API keys (see the table above). No keys yet? Services are simulated; `--mock` adds sample text.

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

An AI agent runs the steps (on Grok Bot: see GROK.md and `skills/social-manager/SKILL.md`). By hand:

```bash
python main.py --tool steps                                                  # every step and its input
python main.py --business sweet-crumbs --tool plan_posts --args '{"task": "new chocolate cake"}'
#   -> "needs_text": rules + data. Write the post, then run it again with "posts": [...]
python main.py --mock --business sweet-crumbs --tool plan_week               # demo with sample text
```

More options:

```bash
python main.py --publish              # Publisher only: post what is due, then stop
python main.py --check                # which services are real or simulated
python main.py --bot                  # always on: phone approvals, reminders, Publisher on time
python main.py --telegram-setup       # check the Telegram bot and find your chat ID
python main.py --once                 # one --bot round, then exit (Grok Bot routine or cron)
```

Run `python main.py --once` every 15 minutes (Grok Bot routine, cron or Windows Task Scheduler),
so posts go out on time.

## 7. See the results

Everything is saved in `data/businesses/<business name>/`:

- `brand_brief.json` - what the Interviewer learned
- `connected_accounts.json` - linked apps
- `content_calendar.json` - every post, its time, script, draft and status
- `content_plan.json` - the Planner's plan, ready for the Creator
- `media_library/<post id>/` - script, drafts, images, clips, voiceover, final videos
- `learnings.json` - what works best (the Planner reads it)
- `followers.json` - follower count per platform, one per day
- `reports.json` - the weekly reports sent to you
- `experiments.json` - the tests: running, the queue of ideas, results
- `alerts.json` - messages for you (failed posts, hard comments, approval reminders)
- `data/businesses/_telegram_state.json` - the bot's memory (which button belongs to which draft)

## 8. Run the tests

```bash
python -m unittest -v
```

The tests need no keys. They check the full loop, the Planner, the Creator, and every real integration
against a fake server (MachGen, OpenAI, Runway, Fish Audio, Groq, Instagram, Facebook, Discord, Telegram, webhook, web search),
plus real ffmpeg edits with captions and exact sizes per platform.

## Settings (in .env)

| Name | Default | What it does |
|---|---|---|
| `MOCK` | empty | `1` = fixed sample text instead of text from the agent (demo and tests) |
| `MACHGEN_IMAGE_MODEL` | `Nano-Banana-2` | MachGen image model |
| `MACHGEN_VIDEO_MODEL` | `Vidu-Q3-Turbo` | MachGen video model |
| `MACHGEN_VIDEO_HEIGHT` | `540` | Video height (nearest allowed height is used) |
| `IMAGE_PROVIDER` / `VIDEO_PROVIDER` | `auto` | `auto` = MachGen if its key is set. Or `openai` / `runway` |
| `VIDEO_MODE` | `image` | `image` = AI image first, then animate it. `text` = text to video |
| `AI_VIDEO_ENABLED` | `1` | `0` = no AI video clips |
| `SLIDESHOW_FALLBACK` | `1` | No AI video: make reels from AI images with a slow zoom |
| `BURN_IN_CAPTIONS` | `1` | Burn captions into videos (`.srt` is always saved) |
| `CAPTION_FONT` | empty | Path to a `.ttf` brand font for captions |
| `FISH_VOICE_ID` | COLD CASE narrator | Fish voice `reference_id` for voiceovers |
| `OPENAI_IMAGE_QUALITY` | `medium` | OpenAI image quality (only if you use OpenAI) |
| `RUNWAY_VIDEO_MODEL` | `gen4.5` | Runway model (only if you use Runway) |
| `APPROVAL_WAIT_SECONDS` | `300` | How long the chat waits for your answers on the phone |
| `APPROVAL_REMINDER_HOURS` | `2` | Reminder before the post time if a draft is not approved |
| `BOT_PUBLISH_EVERY_MINUTES` | `5` | `--bot`: minutes between Publisher runs |
| `TIMEZONE` | `UTC` | Default time zone for posting times |
| `PLANNER_MIN_LEAD_HOURS` | `3` | Time between planning and the first possible post |
| `ANALYST_MIN_REACH` | `100` | Posts seen by fewer people are not scored |
| `ANALYST_MIN_POSTS` | `3` | Posts per group before the Analyst names a "best" format or hour |
| `REPORT_DAY` / `REPORT_HOUR` | `Monday` / `9` | When the weekly report goes to you (business time zone) |
| `TEST_SHARE` | `0.3` | Part of each week's posts that are test posts. `0` = no tests |
| `TEST_POSTS` | `2` | Test posts before a decision (+1 when unclear) |
| `TEST_CHECKPOINT` | `3d` | Post age at which test posts are judged: `24h`, `3d` or `7d` |
| `PUBLISH_RETRY_WAIT` | `2` | Seconds between Publisher retries |
| `SIMULATE_FAIL_RATE` | `0.0` | Test failures (simulated platforms): `1.0` = every post fails |

## Project files

```
rules/CREATIVE_RULES.md     prompt, image, video and voice rules + the self-check (from the CB reels lessons)
main.py                     the steps for the agent (--tool, --once, --check, --publish, --mock, --bot, --telegram-setup)
social_agent/
  config.py                 settings and keys; services() shows real vs simulated
  business_space.py         the shared Business space (JSON files today)
  experiments.py            tests: the usual way vs one new idea (queue, test posts, decision)
  bot.py                    always-on runner: phone answers, reminders, Publisher on time
  agents/
    manager.py              the steps the agent (Manager) runs: --tool <step>
    interviewer.py          1. Interviewer - brief fields, questions, account check
    planner.py              2. Planner - weekly calendar, news posts, scripts, content_plan.json
    creator.py              3. Creator
    owner_approval.py       4. Owner approval - chat (agent), Discord previews, or Telegram; approve, changes, reminders
    publisher.py            5. Publisher
    analyst.py              6. Analyst
  tools/
    website.py              reads the business website
    ai_models.py            provider layer: images, video clips, voice (picks MachGen / OpenAI / Runway / Fish Audio + Groq)
    machgen.py              MachGen REST API: images and video clips
    media_edit.py           sizes per platform, captions, video edit (Pillow + ffmpeg)
    platforms.py            every platform: Instagram and Facebook (Meta Graph API) here, X and LinkedIn below
    x_api.py                X API v2: posts, media upload, metrics
    linkedin.py             LinkedIn Posts API: posts, image and video upload, comments, likes
    storage.py              media links: Cloudflare R2
    discord.py              draft previews and alerts to a Discord channel
    telegram.py             Telegram Bot API: previews, buttons, replies (the Owner app on the phone)
    notify.py               owner alerts: Telegram, webhook and email
    http.py                 shared HTTP helper
tests/                      test_flow.py, test_planner.py, test_creator.py, test_owner_approval.py, test_integrations.py
```

## Still to do (from the plan)

- Owner app (web and mobile) with the same approve / change buttons. The phone works today through Telegram.
- Postgres, a vector store and a job queue instead of JSON files and cron.
- TikTok and YouTube.

## Problems?

- `ModuleNotFoundError`: turn on the venv (step 3), then do step 4 again.
- A step answers `needs_text`: that is normal. Write the text it asks for and run it again with the text.
- An account shows `needs_login`: its Meta token is wrong or expired. Make a new one, then say `connect my accounts again`.
- Telegram: no preview on the phone -> run `python main.py --telegram-setup`. Check `TELEGRAM_CHAT_ID`.
  `Conflict: terminated by other getUpdates request` -> only run one `--bot` per bot token.
  A video bigger than 50 MB is not shown on the phone; the text and buttons still come.
- `python main.py --check` shows SIMULATED for a service: its key is missing or empty in `.env`.
- A draft has a MachGen warning with `HTTP 400`: the model does not take that size or length.
  Try another `MACHGEN_VIDEO_MODEL` / `MACHGEN_IMAGE_MODEL`, or check your MachGen balance.
- `HTTP 429` from MachGen: too many tasks at once. The app waits and tries again 3 times.

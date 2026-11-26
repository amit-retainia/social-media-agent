# Running on Grok Bot

Same setup as CB Reels: Grok is the brain. It talks with the owner and writes all the text (plans, scripts,
captions, replies, learnings). The Python code has no AI text model inside: it does the rest.

## How it runs
```
Owner ── chat ──▶ Grok Bot "Social Manager" (skill: skills/social-manager)
                      │ runs: main.py --tool plan_week / plan_posts / create_drafts / ...
                      ▼
          Planner ─▶ Creator (MachGen images + video, Fish voice, Groq timings, ffmpeg edit)
                      │ draft shown in the chat, copy to Discord
                      ▼
          Owner says "approve" or what to change, in the chat ─▶ approve_draft / request_changes
                      │
Routine every 15 min: main.py --once
    reminders for drafts not approved ─▶ posts what is due (Instagram, Facebook, X, LinkedIn) ─▶ replies to comments
```
Storage:
- **Text in Dexere**, space "Social Media Agent": tables businesses, posts, drafts, comments, metrics, learnings
  (every table has a `business` column) and the log. Grok writes the rows after each step (`--tool export`) and
  rebuilds a fresh disk from them (`--tool restore`).
- **Media in Cloudflare R2** (`MEDIA_UPLOAD=1`), key `sms/<business>/media_library/<post id>/...`: the paid AI
  images (post images, reel start frames), the final files per size, the owner's photos. Like CB Reels, clips and
  voice stay on disk only. Instagram and Facebook download the media from R2 links.
- `data/businesses/<business>/` on the Bot's disk is only the working copy.

## Setup (once)
1. Put the code at `/workspace/social-media-agent` (git clone, or upload a zip).
2. Add the keys (table below) with the secure secret card, never in the normal chat.
   They go in `/workspace/social-media-agent/.env`. Also add `MEDIA_UPLOAD=1` (the Bot only, like `REELS_UPLOAD`).
   Dexere: connect it in Grok (Connect apps -> custom connector `https://mcp.dexere.ai/mcp`, same as CB Reels).
3. In the bot's chat: "Run `cd /workspace/social-media-agent && bash setup.sh`". It installs the packages and prints
   the missing keys and which services are REAL or SIMULATED.
4. Create the bot "Social Manager". Ask it to save a skill:
   "Read `/workspace/social-media-agent/skills/social-manager/SKILL.md` in full and follow it."
5. In that bot, create a routine every 15 minutes:
   "Run `cd /workspace/social-media-agent && .venv/bin/python main.py --once`. If it says comments wait for a reply,
   follow the 'Routine' part of the social-manager skill. Reply with what was posted and replied, in 3 lines at most."
   Use **Test** once.

After a computer update the Python environment is gone: run `bash setup.sh` again.

## Keys

| Key | Used for | Where to get it |
|---|---|---|
| `MACHGEN_API_KEY` | images (Nano-Banana-2) and video clips (Vidu-Q3-Turbo) | machgen.ai, Profile, API Keys |
| `FISH_API_KEY` | voiceover (s2.1-pro-free, no cost) | fish.audio, API Keys |
| `GROQ_API_KEY` | word times for captions and scene cuts (free tier) | console.groq.com/keys |
| `DISCORD_WEBHOOK_URL` | draft previews and alerts | Discord channel, Edit, Integrations, Webhooks |
| `INSTAGRAM_ACCESS_TOKEN`, `INSTAGRAM_USER_ID` | post to Instagram, replies, numbers | Meta app with "Instagram API with Instagram Login" (same as CB Reels) |
| `FACEBOOK_PAGE_ID`, `FACEBOOK_PAGE_TOKEN` | post to a Facebook Page | Meta app, a Page access token (Graph API Explorer) |
| `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_SECRET` | post to X, numbers (X replies are not read) | developer.x.com, your app, Keys and tokens ("Read and write"). X's API is paid: check the price |
| `LINKEDIN_ACCESS_TOKEN`, `LINKEDIN_AUTHOR_URN` | post to LinkedIn, replies, likes and comments | LinkedIn developer app. Profile: product "Share on LinkedIn", URN `urn:li:person:<id>`. Company page: product "Community Management API" (LinkedIn must approve it), URN `urn:li:organization:<id>`. Token lasts 60 days |
| `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET` | keeps the media; links for Instagram and Facebook | Cloudflare, R2, the new private bucket + an API token with "Object Read & Write" on it only. Lifecycle rule: prefix `sms/`, delete after 120 days |
| `MEDIA_UPLOAD` | `1` on the Bot: keep media in R2 | - |
| `TIMEZONE` (optional) | default posting time zone, e.g. `Asia/Kolkata` | |
| `FISH_VOICE_ID` (optional) | narrator voice (Fish `reference_id`) | default: COLD CASE |

A missing key = that service is simulated. Simulated files are never posted.
Supported: Instagram, Facebook, X and LinkedIn. TikTok and YouTube are not. Add only the keys of the platforms you use.

## More businesses
One `--business` name each. Give each business its own Meta keys by adding `_<NAME>`:
`INSTAGRAM_ACCESS_TOKEN_SWEET_CRUMBS`, `FACEBOOK_PAGE_ID_SWEET_CRUMBS`, `X_ACCESS_TOKEN_SWEET_CRUMBS`,
`LINKEDIN_AUTHOR_URN_SWEET_CRUMBS`, and so on for every key of every platform.

## Notes
- Instagram tokens from Instagram Login last 60 days. Renew them before they end, or posting stops
  (the alert says "connect your instagram account again").
- Telegram still works on a normal computer (`main.py --bot`), but Grok Bot does not use it.

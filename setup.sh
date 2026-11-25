#!/usr/bin/env bash
# Get a fresh computer ready (Grok Bot or any Linux/macOS). Safe to run again.
#   bash setup.sh
# The Bot's Python environment is lost after a computer update, so rerun this then.
set -euo pipefail
cd "$(dirname "$0")"

# 1. Python packages in .venv (ffmpeg comes with imageio-ffmpeg).
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
.venv/bin/python -c "import imageio_ffmpeg, subprocess; subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-version'], check=True, capture_output=True); print('ffmpeg: ok')"

# 2. Keys: from the environment (Bot secrets) or .env. Only names are printed, never values.
missing=()
for k in ANTHROPIC_API_KEY MACHGEN_API_KEY FISH_API_KEY GROQ_API_KEY DISCORD_WEBHOOK_URL \
         INSTAGRAM_ACCESS_TOKEN INSTAGRAM_USER_ID FACEBOOK_PAGE_ID FACEBOOK_PAGE_TOKEN \
         R2_ACCOUNT_ID R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY R2_BUCKET; do
  if [ -z "${!k:-}" ] && ! grep -qE "^$k=.+" .env 2>/dev/null; then missing+=("$k"); fi
done
if [ ${#missing[@]} -gt 0 ]; then
  echo "Missing keys: ${missing[*]}"
else
  echo "Keys: all set"
fi

mkdir -p data/businesses
.venv/bin/python main.py --check

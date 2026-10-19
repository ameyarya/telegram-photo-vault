"""Tests never use your real settings: config.env (your drive, Telegram, state) is ignored for the whole test run."""
import os

os.environ["VAULT_CONFIG"] = os.devnull
for _k in ("DRIVE", "STATE_DIR", "FOLDERS", "GALLERY_DATA", "GALLERY_MODELS", "GALLERY_CERT", "GALLERY_KEY",
           "TG_API_ID", "TG_API_HASH", "TG_CHANNEL_ID"):
    os.environ.pop(_k, None)
for _k in [k for k in os.environ if k.startswith("TG_BOT_TOKEN")]:
    os.environ.pop(_k)

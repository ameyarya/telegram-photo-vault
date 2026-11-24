"""Tests never use your real settings: config.env (your drive, Telegram, state) is ignored for the whole test run."""
import os
import tempfile

os.environ["VAULT_CONFIG"] = os.devnull
for _k in ("DRIVE", "STATE_DIR", "FOLDERS", "GALLERY_DATA", "GALLERY_MODELS", "GALLERY_CERT", "GALLERY_KEY",
           "TG_API_ID", "TG_API_HASH", "TG_CHANNEL_ID", "ICLOUD_EXPORT_FOLDER", "ICLOUD_EXPORT_SINCE", "PHOTOS_LIBRARY", "CLOUD_ONLY_FOLDERS"):
    os.environ.pop(_k, None)
for _k in [k for k in os.environ if k.startswith("TG_BOT_TOKEN")]:
    os.environ.pop(_k)

# index safety copies go to a throwaway folder, never into ~/Library or your real state folder
os.environ["DB_BACKUP_DIRS"] = tempfile.mkdtemp(prefix="vault-test-copies-")

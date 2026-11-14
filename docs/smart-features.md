# Smart search, people, similar shots, sorting

**The easy way:** `./vault smart` does the download and analysis below.

## Smart search, people, similar shots

Runs entirely on your Mac (uses the Apple GPU when available); nothing is sent anywhere.

```sh
.venv/bin/pip install -r requirements-ml.txt
cd gallery && ../.venv/bin/python ml.py download    # ~650 MB, once: OpenCLIP ViT-B/32 + OpenCV YuNet/SFace
../.venv/bin/python ml.py run                        # analyse every photo (resumable; ~1 h per 70k photos)
```

Later runs only process new photos; the Backup page has an "Analyse new photos" button.

## Sorting into categories (optional, uses Claude)

The **Sorting** page has Claude Code look at small previews of your photos and file each one under Selfies, People, Food,
Travel, Nature, Pets, Documents, Screens & memes or Other. The categories then appear in the filter menu above your photos.

1. Install [Claude Code](https://claude.com/claude-code) and sign in once in Terminal: `claude auth login`
2. Open the **Sorting** page and press **Try it on 90 photos** (one Claude call), then **Sort all** if you like the result.

Unlike smart search, **this sends the previews to Anthropic** (through the `claude` command on your Mac), so nothing runs unless
you press a button. It uses your Claude plan's usage (about one call per 90 photos) and only labels photos, never videos.
Labels are stored in the gallery's database: no files are moved or copied. Idea from
[claude-photo-sorter](https://github.com/kairothq/claude-photo-sorter).

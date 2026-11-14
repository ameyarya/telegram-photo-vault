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

## Sorting into categories (optional, on your Mac)

The **Sorting** page files each photo under Selfies, People, Food, Travel, Nature, Pets & animals, Documents, Screens & memes
or Other. The categories then appear in the filter menu above your photos.

1. Do the smart-search setup above once (`./vault smart`): sorting reuses its analysis of your photos.
2. Open the **Sorting** page and press **Sort 100 at once**, then **Sort all** if you like the result.

Nothing is sent anywhere. Each photo's CLIP fingerprint (already made for smart search) is compared with a few
descriptions of every category, and the faces the gallery already found decide Selfies vs People. It's quick: a big
library takes a minute or two. It only labels photos, never videos, and no files are moved or copied (labels live in the
gallery's database). It's a good guess, not a perfect one: unusual photos land in Other. Idea from
[claude-photo-sorter](https://github.com/kairothq/claude-photo-sorter), but done locally.

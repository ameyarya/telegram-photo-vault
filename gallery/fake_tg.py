"""A stand-in for Telegram used by the tests (GALLERY_FAKE_TG=<folder>).

The folder holds messages.json — [{"id": 1, "caption": "<drive path>", "file": "<file in the folder>"}] — and the
files themselves. It implements just the Telethon calls the gallery uses, so streaming, syncing and restoring can be
tested without a real account.
"""
import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace


class FakeTelegram:
    def __init__(self, folder):
        self.folder = Path(folder)

    def _messages(self):
        return [self._msg(m) for m in json.loads((self.folder / "messages.json").read_text())]

    def _msg(self, m):
        path = self.folder / m["file"]
        doc = SimpleNamespace(path=path, size=path.stat().st_size)
        return SimpleNamespace(id=m["id"], message=m["caption"], document=doc,
                               file=SimpleNamespace(name=os.path.basename(m["caption"]), size=doc.size))

    async def connect(self):
        return True

    async def disconnect(self):
        return None

    async def is_user_authorized(self):
        return True

    async def start(self, *a, **k):
        return self

    async def get_dialogs(self):
        return []

    async def get_entity(self, peer):
        return SimpleNamespace(id=getattr(peer, "channel_id", 0), title="Fake channel")

    async def get_messages(self, channel, ids=None):
        found = {m.id: m for m in self._messages()}
        if isinstance(ids, (list, tuple)):
            return [found.get(i) for i in ids]
        return found.get(ids)

    async def iter_messages(self, channel, min_id=0, reverse=False):
        msgs = sorted((m for m in self._messages() if m.id > min_id), key=lambda m: m.id, reverse=not reverse)
        for m in msgs:
            yield m

    async def iter_download(self, document, offset=0, request_size=512 * 1024, **_):
        with open(document.path, "rb") as f:
            f.seek(offset)
            while chunk := f.read(request_size):
                yield chunk

    async def download_media(self, msg, file):
        shutil.copyfile(msg.document.path, file)
        return file

"""Telegram side: login (run in Terminal once) and syncing captions -> message ids.

Login:  ../.venv/bin/python tg.py login     (uses TG_API_ID / TG_API_HASH from config.env; asks for phone + code)
Sync:   ../.venv/bin/python tg.py sync      (the server also does this every few minutes)
"""
import asyncio
import os
import sys

from telethon import TelegramClient
from telethon.tl.types import PeerChannel

from config import SESSION, STATE, load_env
from db import connect, link_uploads


def client_from_env():
    cfg = load_env()
    if not cfg["api_id"] or not cfg["api_hash"]:
        return None, cfg
    return TelegramClient(SESSION, cfg["api_id"], cfg["api_hash"]), cfg


async def channel_entity(client, cfg):
    try:
        return await client.get_entity(PeerChannel(cfg["channel_id"]))
    except ValueError:  # not in the session cache yet
        await client.get_dialogs()
        return await client.get_entity(PeerChannel(cfg["channel_id"]))


def _read_last():
    con = connect()
    row = con.execute("SELECT value FROM meta WHERE key='last_msg_id'").fetchone()
    has_msgs = con.execute("SELECT 1 FROM tg_msgs LIMIT 1").fetchone()
    return int(row["value"]) if row and has_msgs else 0  # rebuild from scratch if the table is new


def _save(pairs, last):
    con = connect()
    con.executemany("INSERT OR REPLACE INTO tg_msgs(path, msg_id) VALUES(?, ?)", pairs)
    con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('last_msg_id', ?)", (str(last),))
    con.commit()
    return link_uploads(con)


async def sync(client, cfg, log=print):
    """Record every upload's caption (its drive path) -> message id, then link them to gallery items.
    Database work runs in a thread so a busy index never freezes the web server."""
    channel = await channel_entity(client, cfg)
    last = await asyncio.to_thread(_read_last)
    pairs, linked = [], 0
    async for msg in client.iter_messages(channel, min_id=last, reverse=True):
        last = max(last, msg.id)
        if msg.file and msg.message:
            pairs.append((msg.message.strip(), msg.id))
        if len(pairs) >= 1000:
            linked += await asyncio.to_thread(_save, pairs, last)
            pairs = []
    linked += await asyncio.to_thread(_save, pairs, last)
    log(f"telegram sync: {linked} gallery items linked (last msg {last})")
    return linked


async def login():
    client, cfg = client_from_env()
    if client is None or not cfg["channel_id"]:
        sys.exit("Fill in TG_API_ID, TG_API_HASH and TG_CHANNEL_ID in config.env first (see config.example.env).")
    STATE.mkdir(parents=True, exist_ok=True)
    await client.start()  # prompts for phone number, code (sent in the Telegram app) and 2FA password
    me = await client.get_me()
    channel = await channel_entity(client, cfg)
    print(f"\nLogged in as {me.first_name}. Backup channel found: {channel.title}")
    os.chmod(SESSION + ".session", 0o600)
    await client.disconnect()


async def main():
    if sys.argv[1:] == ["login"]:
        await login()
    elif sys.argv[1:] == ["sync"]:
        client, cfg = client_from_env()
        if client is None:
            sys.exit("Not logged in. Run: ../.venv/bin/python tg.py login")
        async with client:
            await sync(client, cfg)
    else:
        print(__doc__)


if __name__ == "__main__":
    asyncio.run(main())

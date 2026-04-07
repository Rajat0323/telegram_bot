import os
import sys

from dotenv import load_dotenv
from telegram import Bot


load_dotenv()


def main() -> None:
    token = os.getenv("BOT_TOKEN", "")
    if not token:
        raise ValueError("BOT_TOKEN is not configured")

    bot = Bot(token=token)

    async def runner() -> None:
        updates = await bot.get_updates()
        if not updates:
            print("No updates found. Group ya channel me message bhejo, phir script dubara chalao.")
            return

        seen = set()
        for update in updates:
            chat = None
            if update.message:
                chat = update.message.chat
            elif update.channel_post:
                chat = update.channel_post.chat

            if not chat:
                continue

            key = (chat.id, chat.type, chat.title or chat.username or "")
            if key in seen:
                continue
            seen.add(key)
            print(f"chat_id={chat.id} | type={chat.type} | name={chat.title or chat.username or 'private'}")

    if sys.version_info >= (3, 7):
        import asyncio

        asyncio.run(runner())
    else:
        raise RuntimeError("Python 3.7+ required")


if __name__ == "__main__":
    main()

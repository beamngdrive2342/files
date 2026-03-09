import asyncio
from typing import Any, Awaitable, Callable, Dict, List, Union

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

class AlbumMiddleware(BaseMiddleware):
    def __init__(self, latency: Union[int, float] = 0.5):
        self.latency = latency
        self.albums: Dict[str, List[Message]] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message):
            return await handler(event, data)
            
        if not event.media_group_id:
            return await handler(event, data)

        try:
            self.albums[event.media_group_id].append(event)
            return None # Drop so we only process once per album
        except KeyError:
            self.albums[event.media_group_id] = [event]
            await asyncio.sleep(self.latency)

            album = self.albums.pop(event.media_group_id, [])
            if not album:
                return await handler(event, data)
                
            data["album"] = album
            
            # The event passed is the first message in the album,
            # Handler will process the entire album using data['album']
            return await handler(event, data)

"""Reading and writing the mail cache.

The sync service does the network work and hands the results here; the console
and the assistant read from here. Every query is scoped by ``user_id`` — one
Winnie user must never see another's mail — and bodies stay out of list results
until someone opens a message.
"""

from __future__ import annotations

import uuid

from datetime import datetime, timezone

from sqlalchemy import delete, func, nullslast, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from winnie_mail import client
from winnie_mail.models import MailFolder, MailMessage, utcnow


class MailCache:
    """The database side of mail: folders, cursors and cached messages."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    # -- folders and cursors ------------------------------------------------

    async def folders(self, user_id: str, protocol: str) -> list[dict]:
        async with self.session_factory() as session:
            rows = list(
                await session.scalars(
                    select(MailFolder)
                    .where(MailFolder.user_id == user_id, MailFolder.protocol == protocol)
                    .order_by(MailFolder.name)
                )
            )
        return [row.view() for row in rows]

    async def folder(self, user_id: str, protocol: str, name: str) -> dict | None:
        async with self.session_factory() as session:
            row = await self._folder(session, user_id, protocol, name)
        return row.view() if row is not None else None

    async def known_uids(self, user_id: str, protocol: str, folder: str) -> set[str]:
        async with self.session_factory() as session:
            values = await session.scalars(
                select(MailMessage.uid).where(
                    MailMessage.user_id == user_id,
                    MailMessage.protocol == protocol,
                    MailMessage.folder == folder,
                )
            )
        return set(values)

    @staticmethod
    async def _folder(
        session: AsyncSession, user_id: str, protocol: str, name: str
    ) -> MailFolder | None:
        return await session.scalar(
            select(MailFolder).where(
                MailFolder.user_id == user_id,
                MailFolder.protocol == protocol,
                MailFolder.name == name,
            )
        )

    async def apply(
        self, user_id: str, snapshot: client.FolderSnapshot, *, protocol: str
    ) -> None:
        """Merge one folder's snapshot into the cache and advance its cursor."""
        info = snapshot.folder
        async with self.session_factory() as session:
            folder = await self._folder(session, user_id, protocol, info.name)
            if folder is None:
                folder = MailFolder(user_id=user_id, protocol=protocol, name=info.name)
                session.add(folder)
            folder.display_name = info.display_name or info.name
            folder.delimiter = info.delimiter
            folder.selectable = info.selectable
            if snapshot.reset:
                await session.execute(
                    delete(MailMessage).where(
                        MailMessage.user_id == user_id,
                        MailMessage.protocol == protocol,
                        MailMessage.folder == info.name,
                    )
                )
            for message in snapshot.fetched:
                await self._upsert(session, user_id, protocol, info.name, message)
            for uid, unread in snapshot.flags.items():
                await session.execute(
                    update(MailMessage)
                    .where(
                        MailMessage.user_id == user_id,
                        MailMessage.protocol == protocol,
                        MailMessage.folder == info.name,
                        MailMessage.uid == uid,
                    )
                    .values(unread=unread)
                )
            if snapshot.removed:
                await session.execute(
                    delete(MailMessage).where(
                        MailMessage.user_id == user_id,
                        MailMessage.protocol == protocol,
                        MailMessage.folder == info.name,
                        MailMessage.uid.in_(snapshot.removed),
                    )
                )
            # A freshly inserted row still holds None for its server defaults
            # until the flush, so nothing here may assume an int.
            folder.uid_validity = int(snapshot.uid_validity or folder.uid_validity or 0)
            folder.last_uid = max(int(folder.last_uid or 0), int(snapshot.high_uid or 0))
            folder.total = max(snapshot.total, 0)
            folder.last_error = ""
            folder.last_synced_at = utcnow()
            await session.flush()
            folder.unread = int(
                await session.scalar(
                    select(func.count())
                    .select_from(MailMessage)
                    .where(
                        MailMessage.user_id == user_id,
                        MailMessage.protocol == protocol,
                        MailMessage.folder == info.name,
                        MailMessage.unread.is_(True),
                    )
                )
                or 0
            )
            await session.commit()

    async def record_error(
        self, user_id: str, protocol: str, folder: client.FolderInfo, error: str
    ) -> None:
        async with self.session_factory() as session:
            row = await self._folder(session, user_id, protocol, folder.name)
            if row is None:
                row = MailFolder(
                    user_id=user_id, protocol=protocol, name=folder.name
                )
                session.add(row)
            row.display_name = row.display_name or folder.display_name or folder.name
            row.last_error = error[:500]
            await session.commit()

    async def prune_folders(
        self, user_id: str, protocol: str, keep: set[str]
    ) -> int:
        """Drop folders (and their messages) the server no longer offers."""
        async with self.session_factory() as session:
            rows = list(
                await session.scalars(
                    select(MailFolder).where(
                        MailFolder.user_id == user_id, MailFolder.protocol == protocol
                    )
                )
            )
            stale = [row for row in rows if row.name not in keep]
            for row in stale:
                await session.execute(
                    delete(MailMessage).where(
                        MailMessage.user_id == user_id,
                        MailMessage.protocol == protocol,
                        MailMessage.folder == row.name,
                    )
                )
                await session.delete(row)
            await session.commit()
        return len(stale)

    async def purge(self, user_id: str) -> None:
        async with self.session_factory() as session:
            await session.execute(delete(MailMessage).where(MailMessage.user_id == user_id))
            await session.execute(delete(MailFolder).where(MailFolder.user_id == user_id))
            await session.commit()

    # -- messages -----------------------------------------------------------

    async def _upsert(
        self,
        session: AsyncSession,
        user_id: str,
        protocol: str,
        folder: str,
        message: client.HeaderMessage,
    ) -> None:
        row = await session.scalar(
            select(MailMessage).where(
                MailMessage.user_id == user_id,
                MailMessage.protocol == protocol,
                MailMessage.folder == folder,
                MailMessage.uid == message.uid,
            )
        )
        if row is None:
            row = MailMessage(
                user_id=user_id, protocol=protocol, folder=folder, uid=message.uid
            )
            session.add(row)
        row.subject = message.subject
        row.sender = message.sender
        row.recipients = message.to
        row.message_id = message.message_id
        row.unread = message.unread
        row.size = message.size or row.size
        row.date = _parse_date(message.date) or row.date

    async def list_messages(
        self,
        user_id: str,
        *,
        folder: str = "",
        query: str = "",
        unread: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        statement = select(MailMessage).where(MailMessage.user_id == user_id)
        if folder:
            statement = statement.where(MailMessage.folder == folder)
        if unread is not None:
            statement = statement.where(MailMessage.unread.is_(unread))
        if query:
            pattern = f"%{query.strip()}%"
            statement = statement.where(
                or_(
                    MailMessage.subject.ilike(pattern),
                    MailMessage.sender.ilike(pattern),
                    MailMessage.recipients.ilike(pattern),
                    MailMessage.snippet.ilike(pattern),
                    MailMessage.body.ilike(pattern),
                )
            )
        statement = (
            statement.order_by(nullslast(MailMessage.date.desc()), MailMessage.uid.desc())
            .limit(max(1, min(limit, 200)))
            .offset(max(0, offset))
        )
        async with self.session_factory() as session:
            rows = list(await session.scalars(statement))
            labels = await self._labels(session, user_id)
        return [self._labelled(row.view(), labels) for row in rows]

    async def message(self, user_id: str, message_id: str) -> dict | None:
        try:
            key = uuid.UUID(message_id)
        except (TypeError, ValueError):
            return None
        async with self.session_factory() as session:
            row = await session.scalar(
                select(MailMessage).where(
                    MailMessage.id == key, MailMessage.user_id == user_id
                )
            )
            if row is None:
                return None
            labels = await self._labels(session, user_id)
            return self._labelled(row.view(with_body=True), labels)

    @staticmethod
    async def _labels(session: AsyncSession, user_id: str) -> dict[str, str]:
        """Folder name to its human label, for the rows that carry the raw name.

        IMAP folder names arrive in modified UTF-7 (``&XfJT0ZAB-`` is 已发送), so
        the list of a message showing its raw folder name looks like mojibake.
        """
        rows = await session.execute(
            select(MailFolder.protocol, MailFolder.name, MailFolder.display_name).where(
                MailFolder.user_id == user_id
            )
        )
        return {
            f"{protocol}:{name}": display_name or name
            for protocol, name, display_name in rows
        }

    @staticmethod
    def _labelled(payload: dict, labels: dict[str, str]) -> dict:
        key = f"{payload.get('protocol')}:{payload.get('folder')}"
        payload["folder_display"] = labels.get(key) or payload.get("folder") or ""
        return payload

    async def store_body(
        self, user_id: str, message_id: str, message: client.MailMessage
    ) -> dict | None:
        try:
            key = uuid.UUID(message_id)
        except (TypeError, ValueError):
            return None
        async with self.session_factory() as session:
            row = await session.scalar(
                select(MailMessage).where(
                    MailMessage.id == key, MailMessage.user_id == user_id
                )
            )
            if row is None:
                return None
            row.body = message.body
            row.snippet = message.snippet or row.snippet
            row.unread = message.unread
            row.body_fetched_at = utcnow()
            await session.commit()
            labels = await self._labels(session, user_id)
            return self._labelled(row.view(with_body=True), labels)

    async def last_synced(self, user_id: str) -> datetime | None:
        async with self.session_factory() as session:
            return await session.scalar(
                select(func.max(MailFolder.last_synced_at)).where(
                    MailFolder.user_id == user_id
                )
            )

    async def stats(self, user_id: str) -> dict:
        async with self.session_factory() as session:
            total = int(
                await session.scalar(
                    select(func.count())
                    .select_from(MailMessage)
                    .where(MailMessage.user_id == user_id)
                )
                or 0
            )
            unread = int(
                await session.scalar(
                    select(func.count())
                    .select_from(MailMessage)
                    .where(
                        MailMessage.user_id == user_id, MailMessage.unread.is_(True)
                    )
                )
                or 0
            )
            folders = int(
                await session.scalar(
                    select(func.count())
                    .select_from(MailFolder)
                    .where(MailFolder.user_id == user_id)
                )
                or 0
            )
        return {"messages": total, "unread": unread, "folders": folders}


def _parse_date(value: str):
    """The header date as naive UTC, the convention every table here uses."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed

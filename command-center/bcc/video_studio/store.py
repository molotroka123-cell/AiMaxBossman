"""Project/history persistence on the canonical BCC database and transactions."""
from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
import hashlib
import json

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from bcc.db import metadata, utcnow
from .commands import apply_command
from .model import Conflict, EditLocked, MissingObject, StudioError, identity, migrate, new_project

projects = sa.Table("video_studio_projects", metadata,
    sa.Column("id", sa.String(96), primary_key=True), sa.Column("revision", sa.Integer, nullable=False),
    sa.Column("document", sa.JSON, nullable=False), sa.Column("undo_stack", sa.JSON, nullable=False),
    sa.Column("redo_stack", sa.JSON, nullable=False), sa.Column("archived", sa.Boolean, nullable=False, default=False),
    sa.Column("updated_at", sa.DateTime, nullable=False))
revisions = sa.Table("video_studio_revisions", metadata,
    sa.Column("project_id", sa.String(96), primary_key=True), sa.Column("revision", sa.Integer, primary_key=True),
    sa.Column("operation_id", sa.String(96), nullable=False), sa.Column("request_hash", sa.String(64), nullable=False),
    sa.Column("document", sa.JSON, nullable=False), sa.Column("command", sa.JSON, nullable=False),
    sa.Column("actor", sa.String(120), nullable=False), sa.Column("result", sa.JSON, nullable=False),
    sa.Column("created_at", sa.DateTime, nullable=False),
    sa.UniqueConstraint("project_id", "operation_id", name="uq_video_project_operation"))
leases = sa.Table("video_studio_edit_leases", metadata,
    sa.Column("project_id", sa.String(96), primary_key=True), sa.Column("object_id", sa.String(96), primary_key=True),
    sa.Column("actor", sa.String(120), nullable=False), sa.Column("expires_at", sa.DateTime, nullable=False))


def _hash(value):
    try:
        data = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise StudioError("Command must be finite JSON") from exc
    return hashlib.sha256(data.encode()).hexdigest()


def _result(project, operation_id, changed_ids, warnings=(), dry_run=False, can_undo=True):
    return {"project_id": project["id"], "revision": project["revision"], "project": project,
            "changed_ids": list(changed_ids), "warnings": list(warnings), "artifacts": [],
            "undo": {"available": can_undo, "operation_id": operation_id}, "dry_run": dry_run}


class ProjectStore:
    def __init__(self, db):
        self.db = db

    async def _write_lock(self, session, project_id):
        # The lease check and revision commit must have one serialization point.
        # A process-local asyncio lock cannot protect another BCC worker.
        if self.db.url.startswith("sqlite"):
            await session.execute(sa.text("BEGIN IMMEDIATE"))
        else:
            await session.execute(sa.select(projects.c.id).where(
                projects.c.id == project_id).with_for_update())

    async def _row(self, session, project_id):
        result = (await session.execute(sa.select(projects).where(projects.c.id == identity(project_id)))).mappings().first()
        if result is None:
            raise MissingObject("Project does not exist")
        return dict(result)

    async def _replay(self, session, project_id, operation_id, request_hash):
        row = (await session.execute(sa.select(revisions.c.request_hash, revisions.c.result).where(
            revisions.c.project_id == project_id, revisions.c.operation_id == operation_id))).mappings().first()
        if row is None:
            return None
        if row["request_hash"] != request_hash:
            raise Conflict("Operation ID was already used for different arguments")
        return deepcopy(row["result"])

    async def create(self, project_id, name, operation_id, links=None):
        identity(project_id)
        identity(operation_id)
        command = {"type": "project.create", "name": name, "links": links or {}}
        request_hash = _hash(command)
        async with self.db.session() as session:
            prior = await self._replay(session, project_id, operation_id, request_hash)
            if prior:
                return prior
            doc = new_project(project_id, name, links)
            result = _result(doc, operation_id, [project_id], can_undo=False)
            try:
                await session.execute(sa.insert(projects).values(id=project_id, revision=0, document=doc,
                    undo_stack=[], redo_stack=[], archived=False, updated_at=utcnow()))
                await session.execute(sa.insert(revisions).values(project_id=project_id, revision=0,
                    operation_id=operation_id, request_hash=request_hash, document=doc, command=command,
                    actor="human", result=result, created_at=utcnow()))
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                prior = await self._replay(session, project_id, operation_id, request_hash)
                if prior:
                    return prior
                raise Conflict("Project ID already exists") from exc
            return result

    async def get(self, project_id):
        async with self.db.session() as session:
            return migrate((await self._row(session, project_id))["document"])

    async def list(self, archived=False):
        async with self.db.session() as session:
            rows = (await session.execute(sa.select(projects).where(projects.c.archived == archived)
                                           .order_by(projects.c.updated_at.desc()))).mappings().all()
            return [{"id": r["id"], "name": r["document"]["name"], "revision": r["revision"],
                     "archived": r["archived"], "updated_at": r["updated_at"].isoformat(),
                     "links": r["document"].get("links", {})} for r in rows]

    async def history(self, project_id):
        async with self.db.session() as session:
            await self._row(session, project_id)
            rows = (await session.execute(sa.select(revisions.c.revision, revisions.c.operation_id,
                revisions.c.command, revisions.c.actor, revisions.c.created_at).where(
                    revisions.c.project_id == project_id).order_by(revisions.c.revision.desc()))).mappings().all()
            return [dict(row, created_at=row["created_at"].isoformat()) for row in rows]

    async def version(self, project_id, revision):
        async with self.db.session() as session:
            result = (await session.execute(sa.select(revisions.c.document).where(
                revisions.c.project_id == identity(project_id), revisions.c.revision == revision))).scalar_one_or_none()
            if result is None:
                raise MissingObject("Project revision does not exist")
            return migrate(result)

    async def apply(self, project_id, expected_revision, operation_id, command, actor="human", dry_run=False):
        identity(project_id)
        identity(operation_id)
        if type(expected_revision) is not int or expected_revision < 0 or type(dry_run) is not bool:
            raise StudioError("Revision and dry_run must be typed")
        if not isinstance(actor, str) or not actor or len(actor) > 120:
            raise StudioError("Invalid actor")
        request_hash = _hash({"expected_revision": expected_revision, "command": command, "actor": actor})
        async with self.db.session() as session:
            await self._write_lock(session, project_id)
            prior = await self._replay(session, project_id, operation_id, request_hash)
            if prior:
                return prior
            row = await self._row(session, project_id)
            if row["revision"] != expected_revision:
                raise Conflict(f"Revision conflict: expected {expected_revision}, current {row['revision']}")
            old = migrate(row["document"])
            undo, redo = list(row["undo_stack"]), list(row["redo_stack"])
            kind = command.get("type", "").removeprefix("video.")
            if kind in ("history.undo", "history.redo", "history.restore"):
                if kind == "history.restore":
                    target = command.get("revision")
                    undo.append(row["revision"])
                    redo = []
                else:
                    source, destination = (undo, redo) if kind == "history.undo" else (redo, undo)
                    if not source:
                        raise Conflict("There is no operation to undo/redo")
                    target = source.pop()
                    destination.append(row["revision"])
                doc = (await session.execute(sa.select(revisions.c.document).where(
                    revisions.c.project_id == project_id, revisions.c.revision == target))).scalar_one_or_none()
                if doc is None:
                    raise MissingObject("Requested revision is absent from history")
                new = migrate(doc)
                changed = [project_id, *[c["id"] for sq in old["sequences"] for tr in sq["tracks"] for c in tr["clips"]]]
                warnings = []
            else:
                new, changed, warnings = apply_command(old, command, actor)
                undo.append(row["revision"])
                redo = []
            # Leases are concurrency boundaries only; not a replacement for the
            # existing permission/approval layer in the BCC tool executor.
            active = (await session.execute(sa.select(leases).where(leases.c.project_id == project_id,
                leases.c.expires_at > utcnow(), leases.c.actor != actor))).mappings().all()
            affected = set(changed)
            # A track lease covers its clips; relinking an asset also affects
            # every clip using it. Project leases cover all descendants.
            for sq in old["sequences"]:
                for tr in sq["tracks"]:
                    for c in tr["clips"]:
                        if c.get("media_id") in affected or tr["id"] in changed or sq["id"] in changed:
                            affected.add(c["id"])
                        if c["id"] in affected:
                            affected.update([tr["id"], sq["id"]])
            if any(r["object_id"] in affected or r["object_id"] == project_id or project_id in affected for r in active):
                raise EditLocked("A collaborator is editing an affected object; refresh or retry after release")
            new["revision"] = row["revision"] + (0 if dry_run else 1)
            result = _result(new, operation_id, changed, warnings, dry_run, bool(undo))
            if dry_run:
                return result
            update = await session.execute(sa.update(projects).where(projects.c.id == project_id,
                projects.c.revision == expected_revision).values(document=new, revision=new["revision"],
                undo_stack=undo, redo_stack=redo, archived=new["archived"], updated_at=utcnow()))
            if update.rowcount != 1:
                await session.rollback()
                raise Conflict("Another editor committed this revision first")
            try:
                await session.execute(sa.insert(revisions).values(project_id=project_id, revision=new["revision"],
                    operation_id=operation_id, request_hash=request_hash, document=new, command=command,
                    actor=actor, result=result, created_at=utcnow()))
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                prior = await self._replay(session, project_id, operation_id, request_hash)
                if prior:
                    return prior
                raise Conflict("Concurrent operation changed history") from exc
            return result

    async def duplicate(self, project_id, new_id, name, operation_id, expected_revision=None):
        identity(new_id)
        identity(operation_id)
        command = {"type":"project.duplicate", "source":project_id, "name":name,
                   "expected_revision":expected_revision}
        request_hash = _hash(command)
        async with self.db.session() as session:
            prior = await self._replay(session, new_id, operation_id, request_hash)
            if prior:
                return prior
            row = await self._row(session, project_id)
            if expected_revision is not None and row["revision"] != expected_revision:
                raise Conflict("Source project changed before duplication")
            source = migrate(row["document"])
            source.update(id=new_id, name=name, revision=0, archived=False,
                          links={"duplicated_from": project_id})
            source = migrate(source)
            result = _result(source, operation_id, [new_id], can_undo=False)
            try:
                await session.execute(sa.insert(projects).values(id=new_id, document=source, revision=0,
                    undo_stack=[], redo_stack=[], archived=False, updated_at=utcnow()))
                await session.execute(sa.insert(revisions).values(project_id=new_id, revision=0,
                    operation_id=operation_id, request_hash=request_hash, document=source, command=command,
                    actor="human", result=result, created_at=utcnow()))
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                prior = await self._replay(session,new_id,operation_id,request_hash)
                if prior:
                    return prior
                raise Conflict("Duplicate target already exists") from exc
            return result

    async def lease(self, project_id, object_ids, actor="human", ttlseconds=30):
        if not isinstance(object_ids, list) or not object_ids or len(object_ids) > 100:
            raise StudioError("Edit lease requires 1..100 object IDs")
        if type(ttlseconds) is not int or not 1 <= ttlseconds <= 120:
            raise StudioError("Edit lease must last 1..120 seconds")
        if not isinstance(actor,str) or not actor or len(actor)>120:
            raise StudioError("Invalid actor")
        if len(set(object_ids)) != len(object_ids):
            raise StudioError("Edit lease IDs must be unique")
        expires = utcnow()+timedelta(seconds=ttlseconds)
        async with self.db.session() as session:
            await self._write_lock(session, project_id)
            doc = (await self._row(session, project_id))["document"]
            existing_ids = {project_id, *doc["media"].keys()}
            for seq in doc["sequences"]:
                existing_ids.add(seq["id"])
                for tr in seq["tracks"]:
                    existing_ids.add(tr["id"])
                    existing_ids.update(c["id"] for c in tr["clips"])
            related=set(object_ids)
            # Prevent a collaborator acquiring a parent lease around an already
            # leased child (or vice versa). Sibling clips can still be edited.
            for seq in doc["sequences"]:
                for tr in seq["tracks"]:
                    for c in tr["clips"]:
                        if project_id in object_ids or seq["id"] in object_ids or tr["id"] in object_ids or c.get("media_id") in object_ids:
                            related.add(c["id"])
                        if c["id"] in object_ids:
                            related.update([seq["id"],tr["id"]])
                    if tr["id"] in object_ids:
                        related.add(seq["id"])
            active=(await session.execute(sa.select(leases).where(leases.c.project_id==project_id,
                leases.c.expires_at>utcnow(),leases.c.actor!=actor))).mappings().all()
            if any(r["object_id"] in related or r["object_id"]==project_id or project_id in object_ids for r in active):
                raise EditLocked("Another collaborator holds an overlapping edit lease")
            for key in object_ids:
                if identity(key) not in existing_ids:
                    raise MissingObject("Cannot lease an unknown object")
                existing = (await session.execute(sa.select(leases).where(leases.c.project_id==project_id,
                    leases.c.object_id==key))).mappings().first()
                if existing and existing["expires_at"] > utcnow() and existing["actor"] != actor:
                    raise EditLocked("Another collaborator already holds this edit lease")
                if existing:
                    await session.execute(sa.delete(leases).where(leases.c.project_id==project_id, leases.c.object_id==key))
                await session.execute(sa.insert(leases).values(project_id=project_id, object_id=key, actor=actor, expires_at=expires))
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise EditLocked("Another collaborator acquired this edit lease") from exc
        return {"project_id":project_id, "object_ids":object_ids, "actor":actor, "expires_at":expires.isoformat()}

    async def release_lease(self, project_id, actor="human"):
        async with self.db.session() as session:
            await session.execute(sa.delete(leases).where(leases.c.project_id==identity(project_id), leases.c.actor==actor))
            await session.commit()
        return {"released":True}

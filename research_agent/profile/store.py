"""Owner-scoped SQL profile store with transactional versions and snapshots."""
from contextlib import contextmanager
import time
import uuid

from sqlalchemy import JSON, Column, Float, Integer, MetaData, String, Table, delete, insert, select, update

from research_agent.profile.schemas import AttributeDraft

metadata = MetaData()
attributes = Table('person_attributes', metadata,
    Column('id', String(36), primary_key=True), Column('owner_id', String(255), nullable=False, index=True),
    Column('path', JSON, nullable=False), Column('value', JSON, nullable=False),
    Column('basis', String(32), nullable=False), Column('source_refs', JSON, nullable=False),
    Column('scope', String(16), nullable=False), Column('scope_id', String(255)),
    Column('status', String(16), nullable=False), Column('version', Integer, nullable=False),
    Column('created_at', Float, nullable=False), Column('updated_at', Float, nullable=False))
versions = Table('person_attribute_versions', metadata,
    Column('attribute_id', String(36), primary_key=True), Column('version', Integer, primary_key=True),
    Column('owner_id', String(255), nullable=False), Column('snapshot', JSON, nullable=False))


class ProfileConflict(ValueError):
    pass


class ProfileNotFound(ValueError):
    pass


class ProfileStore:
    def __init__(self, engine, owner_id='local'):
        if not owner_id:
            raise ValueError('owner_id is required')
        self.engine, self.owner_id = engine, owner_id
        metadata.create_all(engine)

    @contextmanager
    def transaction(self):
        if self.engine.dialect.name == 'sqlite':
            with self.engine.connect() as conn:
                conn.exec_driver_sql('BEGIN IMMEDIATE')
                try:
                    yield conn
                    conn.commit()
                except BaseException:
                    conn.rollback()
                    raise
        else:
            with self.engine.begin() as conn:
                yield conn

    @staticmethod
    def public(row):
        return {key: value for key, value in row.items() if key != 'owner_id'}

    def owned(self, conn, attribute_id, *, lock=False):
        query = select(attributes).where(attributes.c.id == attribute_id, attributes.c.owner_id == self.owner_id)
        if lock and self.engine.dialect.name == 'postgresql':
            query = query.with_for_update()
        row = conn.execute(query).mappings().first()
        if row is None:
            raise ProfileNotFound('人物属性不存在')
        return dict(row)

    @staticmethod
    def check_version(row, expected_version):
        if row['version'] != expected_version:
            raise ProfileConflict('人物属性已被修改，请刷新后再操作')

    def snapshot(self, conn, row):
        conn.execute(insert(versions).values(attribute_id=row['id'], version=row['version'],
            owner_id=self.owner_id, snapshot=self.public(row)))

    def create(self, draft: AttributeDraft):
        now = time.time()
        row = dict(id=uuid.uuid4().hex, owner_id=self.owner_id, **draft.model_dump(),
                   status='candidate', version=1, created_at=now, updated_at=now)
        with self.transaction() as conn:
            conn.execute(insert(attributes).values(**row))
            self.snapshot(conn, row)
        return self.public(row)

    def list_attributes(self):
        with self.engine.connect() as conn:
            rows = conn.execute(select(attributes).where(attributes.c.owner_id == self.owner_id)
                .order_by(attributes.c.created_at, attributes.c.id)).mappings()
            return [self.public(row) for row in rows]

    def get(self, attribute_id):
        with self.engine.connect() as conn:
            return self.public(self.owned(conn, attribute_id))

    def commit_revision(self, conn, row, changes):
        revised = {**row, **changes, 'version': row['version'] + 1, 'updated_at': time.time()}
        changed = conn.execute(update(attributes).where(attributes.c.id == row['id'],
            attributes.c.owner_id == self.owner_id, attributes.c.version == row['version']).values(**revised))
        if changed.rowcount != 1:
            raise ProfileConflict('人物属性已被修改，请刷新后再操作')
        self.snapshot(conn, revised)
        return self.public(revised)

    def update(self, attribute_id, expected_version, draft: AttributeDraft):
        with self.transaction() as conn:
            row = self.owned(conn, attribute_id, lock=True)
            self.check_version(row, expected_version)
            return self.commit_revision(conn, row, {**draft.model_dump(), 'status': 'candidate'})

    def change_status(self, attribute_id, expected_version, status):
        if status not in {'active', 'stale'}:
            raise ValueError('无效人物属性状态')
        with self.transaction() as conn:
            row = self.owned(conn, attribute_id, lock=True)
            self.check_version(row, expected_version)
            changes = {'status': status}
            if status == 'active':
                changes['basis'] = 'user_confirmed'
            return self.commit_revision(conn, row, changes)

    def history(self, attribute_id):
        with self.engine.connect() as conn:
            self.owned(conn, attribute_id)
            return list(conn.execute(select(versions.c.snapshot).where(
                versions.c.attribute_id == attribute_id, versions.c.owner_id == self.owner_id)
                .order_by(versions.c.version)).scalars())

    def delete(self, attribute_id, expected_version):
        with self.transaction() as conn:
            row = self.owned(conn, attribute_id, lock=True)
            self.check_version(row, expected_version)
            changed = conn.execute(delete(attributes).where(attributes.c.id == attribute_id,
                attributes.c.owner_id == self.owner_id, attributes.c.version == expected_version))
            if changed.rowcount != 1:
                raise ProfileConflict('人物属性已被修改，请刷新后再操作')
            conn.execute(delete(versions).where(versions.c.attribute_id == attribute_id,
                versions.c.owner_id == self.owner_id))

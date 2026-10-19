"""Transactional outbox, consumer inbox and activity metadata."""

import sqlalchemy as sa
from alembic import op

from migrations.events_v1 import NAMING, define_tables

revision = "publications_0002"
down_revision = "publications_0001"
branch_labels = None
depends_on = None


def migration_tables():
    metadata = sa.MetaData(schema="publications", naming_convention=NAMING)
    define_tables(metadata)
    return metadata.sorted_tables


def upgrade():
    for table in migration_tables():
        table.create(bind=op.get_bind())


def downgrade():
    for table in reversed(migration_tables()):
        table.drop(bind=op.get_bind())

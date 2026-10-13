"""Temporary shopping tasks; no rewrite of any learned profile."""
from alembic import op
import sqlalchemy as sa

revision = '20261008_0024'
down_revision = '20261007_0023'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('outfits', sa.Column('intent_constraints_json', sa.JSON(), nullable=True))
    op.create_table('shopping_intents',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('request_id', sa.String(64), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('free_text', sa.Text(), nullable=False),
        sa.Column('constraints_json', sa.JSON(), nullable=False),
        sa.Column('context_json', sa.JSON(), nullable=False),
        sa.Column('active', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('user_id', 'request_id', name='uq_shopping_intent_request'))
    op.create_index('ix_shopping_intents_user_id', 'shopping_intents', ['user_id'])
    op.create_index('ix_shopping_intents_expires_at', 'shopping_intents', ['expires_at'])


def downgrade():
    op.drop_table('shopping_intents')
    op.drop_column('outfits', 'intent_constraints_json')

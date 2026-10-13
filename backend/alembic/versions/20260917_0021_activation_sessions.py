"""Persist Style Swipe progress and first-session product outcomes."""
from alembic import op
import sqlalchemy as sa

revision = '20260917_0021'
down_revision = '20260915_0020'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('activation_sessions',
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id'), primary_key=True),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('segment', sa.String(64), nullable=False),
        sa.Column('shoe_size', sa.String(16), nullable=True),
        sa.Column('swipe_product_ids', sa.JSON(), nullable=False),
        sa.Column('swipes_json', sa.JSON(), nullable=False),
        sa.Column('issued_feed_ids', sa.JSON(), nullable=False),
        sa.Column('wow_json', sa.JSON(), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False))
    op.create_table('activation_exposures',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('activation_sessions.user_id'), nullable=False),
        sa.Column('product_id', sa.String(36), sa.ForeignKey('products.id'), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('reaction', sa.String(16), nullable=True),
        sa.Column('saved', sa.Integer(), nullable=False),
        sa.Column('opened', sa.Integer(), nullable=False),
        sa.Column('clicked', sa.Integer(), nullable=False),
        sa.Column('shown_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('user_id', 'product_id', name='uq_activation_exposure_product'),
        sa.UniqueConstraint('user_id', 'position', name='uq_activation_exposure_position'))
    op.create_index('ix_activation_exposures_user_id', 'activation_exposures', ['user_id'])

def downgrade():
    op.drop_table('activation_exposures')
    op.drop_table('activation_sessions')

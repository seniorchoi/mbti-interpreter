"""Add UserMBTIAnalysis model

Revision ID: a3194d2cdbb5
Revises: 607eee611fdd
Create Date: 2024-10-16 14:25:02.052498

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'a3194d2cdbb5'
down_revision = '607eee611fdd'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('user_mbti_analyses',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('session_id', sa.String(length=36), nullable=False),
    sa.Column('mbti_type', sa.String(length=4), nullable=False),
    sa.Column('explanation', sa.Text(), nullable=True),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('timestamp', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.drop_table('user_mbti_profiles')
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('user_mbti_profiles',
    sa.Column('user_id', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('mbti_type', sa.VARCHAR(length=4), autoincrement=False, nullable=True),
    sa.Column('dominant_function', sa.VARCHAR(length=10), autoincrement=False, nullable=True),
    sa.Column('auxiliary_function', sa.VARCHAR(length=10), autoincrement=False, nullable=True),
    sa.Column('tertiary_function', sa.VARCHAR(length=10), autoincrement=False, nullable=True),
    sa.Column('inferior_function', sa.VARCHAR(length=10), autoincrement=False, nullable=True),
    sa.Column('analysis', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('updated_at', postgresql.TIMESTAMP(), autoincrement=False, nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='user_mbti_profiles_user_id_fkey'),
    sa.PrimaryKeyConstraint('user_id', name='user_mbti_profiles_pkey')
    )
    op.drop_table('user_mbti_analyses')
    # ### end Alembic commands ###

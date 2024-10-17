from app import db
from datetime import datetime
import uuid
import json

class Visitor(db.Model):
    __tablename__ = 'visitor'
    id = db.Column(db.Integer, primary_key=True)
    total_visitors = db.Column(db.Integer, default=0)

class UniqueVisitor(db.Model):
    __tablename__ = 'unique_visitor'
    id = db.Column(db.Integer, primary_key=True)
    unique_visitors = db.Column(db.Integer, default=0)

class ClickCount(db.Model):
    __tablename__ = 'click_count'
    id = db.Column(db.Integer, primary_key=True)
    feature = db.Column(db.String(50), unique=True, nullable=False)
    count = db.Column(db.Integer, default=0)

class UserEmail(db.Model):
    __tablename__ = 'user_email'
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    date_added = db.Column(db.DateTime, default=datetime.utcnow)

class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    auth0_id = db.Column(db.String(100), unique=True, nullable=False)
    email = db.Column(db.String(120), nullable=False)
    insights = db.Column(db.Integer, default=51)
    is_premium = db.Column(db.Boolean, default=False)
    stripe_customer_id = db.Column(db.String(100))
    stripe_subscription_id = db.Column(db.String(100))
    mbti_type = db.Column(db.String(10))

class UserConversation(db.Model):
    __tablename__ = 'user_conversations'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    session_id = db.Column(db.String(36), nullable=False, default=lambda: str(uuid.uuid4()))
    conversation = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref=db.backref('conversations', lazy=True))

class UserMBTIAnalysis(db.Model):
    __tablename__ = 'user_mbti_analyses'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    session_id = db.Column(db.String(36), nullable=False)
    mbti_type = db.Column(db.String(10), nullable=False)
    explanation = db.Column(db.Text)
    confidence = db.Column(db.Float)  # Store confidence level (e.g., 85.0 for 85%)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref=db.backref('mbti_analyses', lazy='dynamic'))

class UserTopic(db.Model):
    __tablename__ = 'user_topics'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    topic = db.Column(db.String(255), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref=db.backref('user_topics', lazy='dynamic'))
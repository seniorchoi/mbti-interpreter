from app import db
from datetime import datetime

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

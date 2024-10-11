from app import db

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

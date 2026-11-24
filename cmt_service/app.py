"""CMT service: conferences, submissions, reviews and decisions."""
import os

from flask import Flask, jsonify

from chair_assignments import chair_bp  # assigning reviewers and progress (T09)
from auth import auth_bp  # register, login, logout (T03)
from conferences import conf_bp  # conferences and committee (T06)
from user_profile import profile_bp  # profile and personal data (T04)
from reviews import review_bp  # reviewing (T10)
from models import db  # the database object from models.py
from decisions import decision_bp  # decisions and results (T11)
from api_security import init_security  # request checks and security headers (T05)
from submissions import submission_bp  # submissions (T07)

# Create the Flask app (the web server for this service)
# static_folder=None: no /static route at all, so an uploaded file can never be served by accident (T08)
app = Flask(__name__, static_folder=None)
init_security(app)

# Where the database is: Docker gives us DATABASE_URL (PostgreSQL).
# Without Docker we fall back to a small local test file (SQLite).
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///cmt_dev.db")

# Connect the database to this app
db.init_app(app)

# Add the account routes (/auth/...) and conference routes
app.register_blueprint(chair_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(profile_bp)
app.register_blueprint(conf_bp)
app.register_blueprint(decision_bp)
app.register_blueprint(review_bp)
app.register_blueprint(submission_bp)

# Create all tables from models.py (does nothing if they already exist)
with app.app_context():
    db.create_all()


# Health check: other servers call this to see if the service is running
@app.route("/health")
def health():
    return jsonify(service="cmt_service", status="ok")


# Start the server when this file is run directly (python app.py)
if __name__ == "__main__":
    # 0.0.0.0 = reachable by the other containers in Docker
    # PORT comes from Docker; 5002 is the default for this service
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5002)))

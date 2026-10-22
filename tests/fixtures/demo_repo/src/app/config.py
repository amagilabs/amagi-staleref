import os

DATABASE_URL = os.environ["DATABASE_URL"]
STRIPE_KEY = os.getenv("STRIPE_SECRET_KEY", "")

import os
import hashlib
from flask import Flask, jsonify

app = Flask(__name__)

@app.route("/", methods=["GET"])
def root():
    # Text verifies CI/CD is deploying the newest image
    return "Hello, Cloud Run! (CI/CD OK)", 200

@app.route("/ping", methods=["GET"])
def ping():
    # SECRET_VALUE is injected via Secret Manager in Cloud Run
    secret_value = os.getenv("SECRET_VALUE")
    present = secret_value is not None and len(secret_value) > 0
    return jsonify({
        "secret_present": present,
        "note": "Locally this is expected to be false. In Cloud Run it will be true."
    }), 200

@app.route("/secret-fingerprint", methods=["GET"])
def secret_fingerprint():
    # Return a non-reversible fingerprint to verify rotation safely
    secret_value = os.getenv("SECRET_VALUE", "")
    sha = hashlib.sha256(secret_value.encode("utf-8")).hexdigest()
    # Only return the hash, never the secret
    return jsonify({"sha256": sha}), 200

def create_app():
    return app

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)

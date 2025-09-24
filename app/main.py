import os
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

def create_app():
    return app

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)

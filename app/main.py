import os
from flask import Flask, jsonify

app = Flask(__name__)

@app.route("/", methods=["GET"])
def root():
    # Basic hello to match Cloud Run validation later
    return "Hello, Cloud Run!", 200

@app.route("/ping", methods=["GET"])
def ping():
    # We'll inject SECRET_VALUE in Cloud Run later
    secret_value = os.getenv("SECRET_VALUE")
    present = secret_value is not None and len(secret_value) > 0
    return jsonify({
        "secret_present": present,
        "note": "Locally this is expected to be false. In Cloud Run it will be true."
    }), 200

def create_app():
    # For WSGI servers if needed later
    return app

if __name__ == "__main__":
    # Local dev server (not for production)
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)

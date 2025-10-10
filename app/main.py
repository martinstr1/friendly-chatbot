import os
from flask import Flask, jsonify
from .routes import bp


app = Flask(__name__)
app.register_blueprint(bp)

@app.route("/", methods=["GET"])
def root():
    return "Hello, Cloud Run! (CI/CD OK)", 200

@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"ok": True}), 200

def create_app():
    return app

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)

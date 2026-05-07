from flask import Flask, request, jsonify
from flask_cors import CORS
from groq import Groq
import os

app = Flask(__name__, static_folder=".", static_url_path="")
CORS(app)

client = Groq(api_key=os.getenv("GROQ_API_KEY"))

@app.route("/")
def home():
    return app.send_static_file("index.html")

@app.route("/chat", methods=["POST"])
def chat():
    try:
        if not os.getenv("GROQ_API_KEY"):
            return jsonify(reply="ERROR: GROQ_API_KEY environment variable not set"), 500

        data = request.get_json(silent=True) or {}
        user_message = str(data.get("message", "")).strip()

        if not user_message:
            return jsonify(reply="Say something first 🦀"), 400

        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": "You are Intelligent Crab Guy 🦀, funny, clever, slightly sarcastic."},
                {"role": "user", "content": user_message}
            ]
        )

        reply = response.choices[0].message.content
        return jsonify(reply=reply)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify(reply=f"ERROR: {str(e)}"), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
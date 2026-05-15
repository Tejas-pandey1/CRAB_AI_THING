from flask import Flask, request, jsonify, render_template, redirect, url_for, flash, session
from flask_cors import CORS
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from groq import Groq
from datetime import datetime
import os
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-key')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///crabchat.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

client = Groq(api_key=os.getenv("GROQ_API_KEY"))

# ─────────────────────────────────────────────
#  Constants
# ─────────────────────────────────────────────
MAX_MEMORIES       = 20      # max permanent memories per user
RECENT_MSG_LIMIT   = 20      # messages pulled into AI context per chat
MAX_MEMORY_CHARS   = 1500    # guard against giant memory blocks in prompt

PERSONALITY_PROMPTS = {
    "fun": (
        "You are Intelligent Crab Guy 🦀 in FUN MODE. "
        "You are hysterically funny, wildly energetic, and lovably chaotic. "
        "Crack crab puns, use lots of emojis, be theatrical and over-the-top. "
        "You still give correct answers, but you wrap them in pure comedy gold. "
        "Snip-snap your claws and GO WILD."
    ),
    "balanced": (
        "You are Intelligent Crab Guy 🦀 in BALANCED MODE. "
        "You are friendly, helpful, and occasionally sprinkle in a light crab joke. "
        "You are conversational and warm, mostly focused on being useful. "
        "A small emoji here and there is fine."
    ),
    "serious": (
        "You are an expert AI assistant. "
        "You are professional, direct, and precise. "
        "No jokes, no emojis, no crab references. "
        "Provide accurate, well-structured, concise answers only."
    ),
}

# ─────────────────────────────────────────────
#  Models
# ─────────────────────────────────────────────
class User(UserMixin, db.Model):
    __tablename__ = "users"
    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    personality   = db.Column(db.String(20), default="balanced", nullable=False)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)

    chats     = db.relationship("Chat",   back_populates="user", cascade="all, delete-orphan")
    memories  = db.relationship("Memory", back_populates="user", cascade="all, delete-orphan")


class Chat(db.Model):
    __tablename__ = "chats"
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    title      = db.Column(db.String(200), default="New Chat")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user     = db.relationship("User",    back_populates="chats")
    messages = db.relationship("Message", back_populates="chat", cascade="all, delete-orphan",
                               order_by="Message.created_at")


class Message(db.Model):
    __tablename__ = "messages"
    id         = db.Column(db.Integer, primary_key=True)
    chat_id    = db.Column(db.Integer, db.ForeignKey("chats.id"), nullable=False)
    role       = db.Column(db.String(20), nullable=False)   # "user" | "assistant"
    content    = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    chat = db.relationship("Chat", back_populates="messages")


class Memory(db.Model):
    __tablename__ = "memories"
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    content    = db.Column(db.String(500), nullable=False)
    priority   = db.Column(db.Integer, default=5)          # 1 (low) – 10 (high)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", back_populates="memories")


# ─────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


def build_system_prompt(user: User) -> str:
    """Build the full system prompt from personality + memories."""
    personality_text = PERSONALITY_PROMPTS.get(user.personality, PERSONALITY_PROMPTS["balanced"])

    memories = (
        Memory.query
        .filter_by(user_id=user.id)
        .order_by(Memory.priority.desc(), Memory.created_at.desc())
        .all()
    )

    if memories:
        mem_lines = "\n".join(f"- {m.content}" for m in memories)
        # Truncate if too big
        if len(mem_lines) > MAX_MEMORY_CHARS:
            mem_lines = mem_lines[:MAX_MEMORY_CHARS] + "\n[...older memories omitted to save space]"
        memory_block = f"\n\n[PERMANENT MEMORIES ABOUT THIS USER]\n{mem_lines}"
    else:
        memory_block = ""

    return personality_text + memory_block


def build_messages_payload(chat: Chat, new_user_message: str, system_prompt: str) -> list:
    """Assemble the messages list for the Groq API call."""
    history = chat.messages[-RECENT_MSG_LIMIT:] if chat.messages else []

    messages = [{"role": "system", "content": system_prompt}]
    for msg in history:
        messages.append({"role": msg.role, "content": msg.content})
    messages.append({"role": "user", "content": new_user_message})
    return messages


def auto_title_chat(chat: Chat, first_message: str):
    """Set chat title from first user message (truncated)."""
    if chat.title == "New Chat" and first_message:
        chat.title = first_message[:60] + ("…" if len(first_message) > 60 else "")
        db.session.commit()


def extract_memories_from_message(user_id: int, text: str):
    """
    Detect memory-worthy phrases and auto-save them.
    Triggers: 'my name is', 'I am', 'remember that', 'I like', 'I love', 'I hate',
              'my favorite', 'I prefer', 'I work', 'I live'.
    """
    triggers = [
        "my name is", "i am ", "remember that", "i like ", "i love ",
        "i hate ", "my favorite", "i prefer", "i work", "i live",
        "don't forget", "keep in mind",
    ]
    lower = text.lower()
    if not any(t in lower for t in triggers):
        return

    # Don't exceed limit – drop lowest-priority memory if needed
    count = Memory.query.filter_by(user_id=user_id).count()
    if count >= MAX_MEMORIES:
        oldest = (
            Memory.query
            .filter_by(user_id=user_id)
            .order_by(Memory.priority.asc(), Memory.created_at.asc())
            .first()
        )
        if oldest:
            db.session.delete(oldest)

    mem = Memory(user_id=user_id, content=text[:500], priority=5)
    db.session.add(mem)
    db.session.commit()


# ─────────────────────────────────────────────
#  Auth Routes
# ─────────────────────────────────────────────
@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("home"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            return redirect(url_for("home"))
        flash("Invalid username or password")
    return render_template("login.html", register=False)


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("home"))
    if request.method == "POST":
        username    = request.form.get("username", "").strip()
        password    = request.form.get("password", "")
        personality = request.form.get("personality", "balanced")

        if not username or not password:
            flash("Username and password are required.")
            return render_template("login.html", register=True)

        if User.query.filter_by(username=username).first():
            flash("Username already taken.")
            return render_template("login.html", register=True)

        if personality not in PERSONALITY_PROMPTS:
            personality = "balanced"

        user = User(
            username=username,
            password_hash=generate_password_hash(password),
            personality=personality,
        )
        db.session.add(user)
        db.session.commit()
        login_user(user)
        return redirect(url_for("home"))
    return render_template("login.html", register=True)


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


# ─────────────────────────────────────────────
#  Main App
# ─────────────────────────────────────────────
@app.route("/")
@login_required
def home():
    return render_template("index.html", user=current_user)


# ─────────────────────────────────────────────
#  Chat API
# ─────────────────────────────────────────────
@app.route("/api/chats", methods=["GET"])
@login_required
def get_chats():
    chats = (
        Chat.query
        .filter_by(user_id=current_user.id)
        .order_by(Chat.updated_at.desc())
        .all()
    )
    return jsonify([
        {"id": c.id, "title": c.title, "updated_at": c.updated_at.isoformat()}
        for c in chats
    ])


@app.route("/api/chats", methods=["POST"])
@login_required
def create_chat():
    chat = Chat(user_id=current_user.id)
    db.session.add(chat)
    db.session.commit()
    return jsonify({"id": chat.id, "title": chat.title})


@app.route("/api/chats/<int:chat_id>", methods=["DELETE"])
@login_required
def delete_chat(chat_id):
    chat = Chat.query.filter_by(id=chat_id, user_id=current_user.id).first_or_404()
    db.session.delete(chat)
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/chats/<int:chat_id>/messages", methods=["GET"])
@login_required
def get_messages(chat_id):
    chat = Chat.query.filter_by(id=chat_id, user_id=current_user.id).first_or_404()
    return jsonify([
        {"id": m.id, "role": m.role, "content": m.content, "created_at": m.created_at.isoformat()}
        for m in chat.messages
    ])


@app.route("/api/chats/<int:chat_id>/messages/<int:msg_id>", methods=["DELETE"])
@login_required
def delete_message(chat_id, msg_id):
    chat = Chat.query.filter_by(id=chat_id, user_id=current_user.id).first_or_404()
    msg  = Message.query.filter_by(id=msg_id, chat_id=chat.id).first_or_404()
    db.session.delete(msg)
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/chats/<int:chat_id>/chat", methods=["POST"])
@login_required
def chat_message(chat_id):
    if not os.getenv("GROQ_API_KEY"):
        return jsonify(error="GROQ_API_KEY not set"), 500

    chat = Chat.query.filter_by(id=chat_id, user_id=current_user.id).first_or_404()

    data         = request.get_json(silent=True) or {}
    user_message = str(data.get("message", "")).strip()
    if not user_message:
        return jsonify(error="Message is empty"), 400

    # Auto-detect memories
    extract_memories_from_message(current_user.id, user_message)

    # Build prompt
    system_prompt    = build_system_prompt(current_user)
    messages_payload = build_messages_payload(chat, user_message, system_prompt)

    try:
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=messages_payload,
            max_tokens=1024,
        )
        reply = response.choices[0].message.content
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify(error=str(e)), 500

    # Persist both turns
    db.session.add(Message(chat_id=chat.id, role="user",      content=user_message))
    db.session.add(Message(chat_id=chat.id, role="assistant", content=reply))
    chat.updated_at = datetime.utcnow()

    # Auto-title on first message
    is_first = len(chat.messages) == 0
    db.session.commit()
    if is_first:
        auto_title_chat(chat, user_message)

    return jsonify(reply=reply, chat_id=chat.id, chat_title=chat.title)


# ─────────────────────────────────────────────
#  Memory API
# ─────────────────────────────────────────────
@app.route("/api/memories", methods=["GET"])
@login_required
def get_memories():
    memories = (
        Memory.query
        .filter_by(user_id=current_user.id)
        .order_by(Memory.priority.desc(), Memory.created_at.desc())
        .all()
    )
    return jsonify([
        {"id": m.id, "content": m.content, "priority": m.priority,
         "created_at": m.created_at.isoformat()}
        for m in memories
    ])


@app.route("/api/memories", methods=["POST"])
@login_required
def add_memory():
    data    = request.get_json(silent=True) or {}
    content = str(data.get("content", "")).strip()
    priority = int(data.get("priority", 5))

    if not content:
        return jsonify(error="Memory content is empty"), 400
    if len(content) > 500:
        return jsonify(error="Memory too long (max 500 chars)"), 400
    if not (1 <= priority <= 10):
        priority = 5

    count = Memory.query.filter_by(user_id=current_user.id).count()
    if count >= MAX_MEMORIES:
        return jsonify(error=f"Memory limit reached ({MAX_MEMORIES}). Delete one first."), 400

    mem = Memory(user_id=current_user.id, content=content, priority=priority)
    db.session.add(mem)
    db.session.commit()
    return jsonify({"id": mem.id, "content": mem.content, "priority": mem.priority})


@app.route("/api/memories/<int:mem_id>", methods=["DELETE"])
@login_required
def delete_memory(mem_id):
    mem = Memory.query.filter_by(id=mem_id, user_id=current_user.id).first_or_404()
    db.session.delete(mem)
    db.session.commit()
    return jsonify({"ok": True})


# ─────────────────────────────────────────────
#  Settings API
# ─────────────────────────────────────────────
@app.route("/api/settings/personality", methods=["POST"])
@login_required
def set_personality():
    data        = request.get_json(silent=True) or {}
    personality = data.get("personality", "balanced")
    if personality not in PERSONALITY_PROMPTS:
        return jsonify(error="Invalid personality"), 400
    current_user.personality = personality
    db.session.commit()
    return jsonify({"ok": True, "personality": personality})


@app.route("/api/settings/me", methods=["GET"])
@login_required
def get_me():
    return jsonify({
        "username":    current_user.username,
        "personality": current_user.personality,
        "memory_count": Memory.query.filter_by(user_id=current_user.id).count(),
        "memory_limit": MAX_MEMORIES,
    })


# ─────────────────────────────────────────────
#  Entry Point
# ─────────────────────────────────────────────
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)


@app.route("/chat", methods=["POST"])
@login_required
def chat_legacy():
    """Legacy endpoint used by the single-file frontend. Finds or creates a chat
    for the current user and forwards the request to the main chat handler.
    """
    # get most recent chat or create one
    chat = (
        Chat.query
        .filter_by(user_id=current_user.id)
        .order_by(Chat.updated_at.desc())
        .first()
    )
    if not chat:
        chat = Chat(user_id=current_user.id)
        db.session.add(chat)
        db.session.commit()

    return chat_message(chat.id)
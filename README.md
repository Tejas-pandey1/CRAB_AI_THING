# Intelligent Crab Guy - AI Chat Assistant

A modern, responsive AI chat application built with Flask and powered by Groq's Llama model.

## Features

- 🤖 AI-powered chat with Intelligent Crab Guy
- 🔐 User authentication system
- 📱 Responsive design for desktop and mobile
- 💾 Persistent user data with SQL database
- 🎨 Beautiful dark theme UI

## Setup

1. Clone the repository
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Create a `.env` file with your environment variables:
   ```
   SECRET_KEY=your-secret-key-here
   GROQ_API_KEY=your-groq-api-key-here
   DATABASE_URL=sqlite:///users.db
   ```

4. Run the application:
   ```bash
   python app.py
   ```

5. Open http://localhost:5000 in your browser

## Deployment

This app is configured for deployment on Render. The `render.yaml` and `Procfile` are included for easy deployment.

For production, consider using a more robust database like PostgreSQL instead of SQLite.

## Usage

- Register a new account or login
- Start chatting with the AI
- Access chat history and settings (coming soon)
- Logout when done

## Technologies Used

- Flask
- SQLAlchemy
- Flask-Login
- Groq API
- HTML/CSS/JavaScript
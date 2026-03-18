from flask import Flask, render_template, request, redirect, url_for, session
import sqlite3
import requests
import json
import re
import os
from dotenv import load_dotenv
import google.generativeai as genai

load_dotenv()

app = Flask(__name__)
app.secret_key = "secret123"

gemini_api_key = os.getenv("geminiapi")
if not gemini_api_key:
    raise ValueError("No Gemini API key found in .env file (geminiapi)")

genai.configure(api_key=gemini_api_key)

def generate_questions(topic, difficulty="Medium"):
    topic_clean = topic.strip().lower()
    if topic_clean in ["common", "anything", "random", "general", "whatever", "mix", "any"]:
        topic_instruction = "a mix of general knowledge, trivia, and common sense topics"
    else:
        topic_instruction = f"'{topic}'"

    prompt = f"Generate 10 multiple choice questions about {topic_instruction} with a '{difficulty}' difficulty level. Return ONLY a JSON array of objects. Each object must have 'question' (string), 'options' (array of exactly 4 strings), and 'answer' (string, must exactly match one of the options). If a question contains block code for the user to read, place the raw code exclusively in a separate field called 'code_snippet' (string), and do NOT use Markdown backticks or put the code in the 'question' field. Do not include any other text or markdown formatting outside of the JSON array."
    model = genai.GenerativeModel('gemini-2.5-flash')
    response = model.generate_content(prompt)
    
    text = response.text
    match = re.search(r'\[.*\]', text, re.DOTALL)
    if match:
        text = match.group(0)
    
    try:
        raw_questions = json.loads(text)
        
        for q in raw_questions:
            q['question'] = str(q['question']).replace('`', '')
            q['options'] = [str(opt).replace('`', '') for opt in q.get('options', [])]
            q['answer'] = str(q['answer']).replace('`', '')
            
        return raw_questions
    except json.JSONDecodeError:
        return []

# ---------------- DATABASE ----------------
def init_db():
    conn = sqlite3.connect("database.db")
    c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT,
        password TEXT
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        score INTEGER,
        total INTEGER,
        date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    try:
        c.execute("ALTER TABLE results ADD COLUMN questions_json TEXT")
        c.execute("ALTER TABLE results ADD COLUMN answers_json TEXT")
    except sqlite3.OperationalError:
        pass

    conn.commit()
    conn.close()

init_db()

# ---------------- QUESTIONS (Dynamic) ----------------

# ---------------- REGISTER ----------------
@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        conn = sqlite3.connect("database.db")
        c = conn.cursor()
        c.execute("INSERT INTO users (username, password) VALUES (?,?)", (username,password))
        conn.commit()
        conn.close()

        return redirect(url_for('login'))

    return render_template('register.html')

# ---------------- LOGIN ----------------
@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        conn = sqlite3.connect("database.db")
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE username=? AND password=?", (username,password))
        user = c.fetchone()
        conn.close()

        if user:
            session['user_id'] = user[0]
            return redirect(url_for('home'))
        else:
            return "Invalid login"

    return render_template('login.html')

# ---------------- HOME ----------------
@app.route('/')
def home():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    return redirect(url_for('dashboard'))



@app.route('/result')
def result():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    questions = session.get('questions', [])
    user_answers = session.get('user_answers', [])

    if not questions:
        return redirect(url_for('dashboard'))

    score = session.get('score', 0)
    total = len(questions)
    
    import json
    q_json = json.dumps(questions)
    a_json = json.dumps(user_answers)

    # Save result
    conn = sqlite3.connect("database.db")
    c = conn.cursor()
    c.execute("INSERT INTO results (user_id, score, total, questions_json, answers_json) VALUES (?,?,?,?,?)",
              (session['user_id'], score, total, q_json, a_json))
    result_id = c.lastrowid
    conn.commit()
    conn.close()
    
    session.pop('questions', None)
    session.pop('user_answers', None)
    session.pop('score', None)
    session.pop('qno', None)

    return redirect(url_for('view_result', result_id=result_id))

@app.route('/result/<int:result_id>')
def view_result(result_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    conn = sqlite3.connect("database.db")
    c = conn.cursor()
    c.execute("SELECT score, total, questions_json, answers_json, date FROM results WHERE id=? AND user_id=?", (result_id, session['user_id']))
    row = c.fetchone()
    conn.close()
    
    if not row:
        return "Result not found", 404
        
    score, total, q_json, a_json, date = row
    
    import json
    questions = json.loads(q_json) if q_json else []
    user_answers = json.loads(a_json) if a_json else []
    
    return render_template('result.html',
                           score=score,
                           total=total,
                           questions=questions,
                           user_answers=user_answers,
                           date=date)


# ---------------- DASHBOARD ----------------
@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = sqlite3.connect("database.db")
    c = conn.cursor()

    c.execute("SELECT username FROM users WHERE id=?", (session['user_id'],))
    user = c.fetchone()
    username = user[0] if user else "User"

    c.execute("SELECT id, score, total, date FROM results WHERE user_id=? ORDER BY date ASC", (session['user_id'],))
    all_data = c.fetchall()

    dates = [row[3][:16] for row in all_data]
    percentages = [round((row[1] / row[2]) * 100) if row[2] > 0 else 0 for row in all_data]
    data_desc = list(reversed(all_data))

    conn.close()

    return render_template('dashboard.html', data=data_desc, username=username, chart_labels=dates, chart_data=percentages)

# ---------------- LOGOUT ----------------
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/category', methods=['GET','POST'])
def category():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        category = request.form['category']
        difficulty = request.form.get('difficulty', 'Medium')
        session['category'] = f"{category} ({difficulty})"

        questions = generate_questions(category, difficulty)
        if not questions:
            return "Failed to generate questions. Please try again."

        session['questions'] = questions
        session['qno'] = 0
        session['score'] = 0
        session['user_answers'] = []

        return redirect(url_for('quiz'))

    return render_template('category.html')

@app.route('/quiz', methods=['GET','POST'])
def quiz():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    questions = session.get('questions', [])
    qno = session.get('qno', 0)

    if request.method == 'POST':
        selected = request.form.get('answer')

        # store user answer
        session['user_answers'].append(selected)

        if selected == questions[qno]['answer']:
            session['score'] += 1

        session['qno'] += 1
        qno = session['qno']

    if qno >= len(questions):
        return redirect(url_for('result'))

    return render_template('quiz.html', q=questions[qno], qno=qno+1)

# ---------------- RUN ----------------
if __name__ == '__main__':
    app.run(debug=True)
import json
import os
import random
import sqlite3
import time
from datetime import datetime, date
from pathlib import Path

import streamlit as st
import anthropic

@st.fragment(run_every="1s")
def render_mock_timer(started):
    elapsed = int(time.time() - started)
    remaining = max(0, 900 - elapsed)

    minutes, seconds = divmod(remaining, 60)
    st.caption(
        f"Time remaining: {minutes:02d}:{seconds:02d}"
    )

    if remaining == 0:
        st.warning(
            "Time is up. Please submit your answers."
        )

BASE = Path(__file__).parent
DB_PATH = BASE / "data" / "g1_smartprep.db"
HANDBOOK_URL = "https://www.ontario.ca/document/official-mto-drivers-handbook"

st.set_page_config(page_title="G1 SmartPrep", page_icon="🚗", layout="wide", initial_sidebar_state="expanded")

# ---------- Persistence ----------
def db():
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            mode TEXT NOT NULL,
            score INTEGER NOT NULL,
            total INTEGER NOT NULL,
            topic_scores TEXT NOT NULL,
            duration_seconds INTEGER,
            confidence INTEGER,
            notes TEXT
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS responses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            attempt_id INTEGER NOT NULL,
            question_id TEXT NOT NULL,
            topic TEXT NOT NULL,
            selected_answer TEXT,
            correct_answer TEXT NOT NULL,
            is_correct INTEGER NOT NULL,
            confidence INTEGER,
            FOREIGN KEY(attempt_id) REFERENCES attempts(id)
        )""")

def save_attempt(mode, question_list, answers, confidences, started_at):
    score = sum(1 for q in question_list if answers.get(q["id"]) == q["answer"])
    total = len(question_list)
    topic = {}
    for q in question_list:
        entry = topic.setdefault(q["category"], {"correct": 0, "total": 0})
        entry["total"] += 1
        entry["correct"] += int(answers.get(q["id"]) == q["answer"])
    duration = int(time.time() - started_at) if started_at else None
    conf_values = [confidences.get(q["id"]) for q in question_list if confidences.get(q["id"]) is not None]
    overall_conf = round(sum(conf_values) / len(conf_values)) if conf_values else None
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO attempts(created_at,mode,score,total,topic_scores,duration_seconds,confidence) VALUES(?,?,?,?,?,?,?)",
            (datetime.now().isoformat(timespec="seconds"), mode, score, total, json.dumps(topic), duration, overall_conf)
        )
        attempt_id = cur.lastrowid
        for q in question_list:
            conn.execute(
                "INSERT INTO responses(attempt_id,question_id,topic,selected_answer,correct_answer,is_correct,confidence) VALUES(?,?,?,?,?,?,?)",
                (attempt_id, q["id"], q["category"], answers.get(q["id"]), q["answer"],
                 int(answers.get(q["id"]) == q["answer"]), confidences.get(q["id"]))
            )
    return score, total, topic, attempt_id

def get_history():
    with db() as conn:
        return conn.execute("SELECT * FROM attempts ORDER BY id DESC").fetchall()

def get_responses():
    with db() as conn:
        return conn.execute("""
            SELECT r.*, a.created_at, a.mode
            FROM responses r JOIN attempts a ON a.id=r.attempt_id
            ORDER BY a.id DESC
        """).fetchall()

init_db()

# ---------- Content and AI ----------
# Topics are labels for AI-generated questions; no static question bank is used.
TOPICS = [
    "Road Signs and Signals",
    "Rules of the Road",
    "Intersections and Right of Way",
    "Safe Driving",
    "Sharing the Road",
    "Parking and Stopping",
    "G1 Restrictions",
]

def api_key():
    try:
        return st.secrets.get("ANTHROPIC_API_KEY", "")
    except Exception:
        return os.getenv("ANTHROPIC_API_KEY", "")


def claude_client():
    key = api_key()
    if not key or key == "PASTE_YOUR_ANTHROPIC_API_KEY_HERE":
        return None
    try:
        return anthropic.Anthropic(api_key=key)
    except Exception:
        return None


CLIENT = claude_client()

SYSTEM = """You are G1 SmartPrep, a careful educational tutor for Ontario G1 knowledge-test study.
The Official MTO Driver's Handbook is the authority. Never invent legal requirements, penalties,
speed limits, road-sign meanings, or right-of-way rules. If you are uncertain or the question
requires a precise current rule, say so and direct the learner to the relevant section of the
Official MTO Driver's Handbook. Clearly distinguish general safe-driving guidance from exact law.
Do not claim practice questions are actual official exam questions. Use plain language and explain
the reasoning. You do not have the full handbook unless passages are supplied in the prompt."""


def ask_ai(prompt, system=SYSTEM):
    if not CLIENT:
        return (
            "Claude AI is not connected. Please check your "
            "ANTHROPIC_API_KEY in .streamlit/secrets.toml."
        )

    for attempt in range(3):
        try:
            response = CLIENT.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1200,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            answer = "\n".join(
                block.text for block in response.content
                if getattr(block, "type", None) == "text"
            )
            return answer or "Claude returned an empty response. Please try again."

        except Exception as exc:
            error = str(exc).upper()
            temporary = any(code in error for code in [
                "429", "RATE_LIMIT", "500", "INTERNAL_SERVER_ERROR",
                "502", "503", "504", "OVERLOADED", "TIMEOUT",
            ])
            if temporary and attempt < 2:
                time.sleep(2 ** (attempt + 1))
                continue

            if "401" in error or "AUTHENTICATION" in error:
                return "Claude authentication failed. Check the API key in .streamlit/secrets.toml."
            if "402" in error or "PAYMENT" in error or "INSUFFICIENT" in error:
                return "Claude API billing or credit limit may have been reached. Check your Anthropic Console usage and billing."
            if "429" in error or "RATE_LIMIT" in error:
                return "Claude is rate-limiting requests. Please wait a moment and try again."
            if "529" in error or "OVERLOADED" in error or "503" in error:
                return "Claude is temporarily busy. Please wait 30–60 seconds and try again. Your saved quiz progress is unaffected."
            return f"Claude API request failed: {exc}\n\nCheck your API key, model access, and billing/usage limits."

    return "Claude is temporarily unavailable. Please try again."


def handbook_chunks(text, chunk_size=900, overlap=150):
    text = " ".join(text.split())
    chunks = []
    start = 0
    while start < len(text):
        chunks.append(text[start:start + chunk_size])
        if start + chunk_size >= len(text):
            break
        start += chunk_size - overlap
    return chunks

def retrieve_handbook(query, chunks, top_k=4):
    # Lightweight lexical retrieval. This is a simple starter RAG-style search, not semantic embeddings.
    terms = {t.lower().strip(".,?!:;()[]{}\"'") for t in query.split() if len(t) > 2}
    scored = []
    for i, chunk in enumerate(chunks):
        lower = chunk.lower()
        score = sum(lower.count(term) for term in terms)
        if score:
            scored.append((score, i, chunk))
    scored.sort(reverse=True)
    return [(i, chunk) for score, i, chunk in scored[:top_k]]

def generate_questions_from_handbook(mode, selected_topics, count):
    """Generate a fresh, source-grounded multiple-choice set with Claude."""
    if not CLIENT:
        return None, "Claude AI is not connected. Check your API key in .streamlit/secrets.toml."

    chunks = st.session_state.get("handbook_chunks", [])
    if not chunks:
        return None, "Upload the current official MTO Driver’s Handbook below before starting a quiz."

    topics = selected_topics or TOPICS
    query = " ".join(topics) + " Ontario G1 driver knowledge test road rules traffic signs safe driving restrictions"
    matches = retrieve_handbook(query, chunks, top_k=min(10, max(5, len(chunks))))
    if not matches:
        # If topic names do not match the handbook's wording, use representative chunks.
        matches = [(i, chunk) for i, chunk in enumerate(chunks[:8])]
    if not matches:
        return None, "No readable handbook text was found. Upload a text-based PDF or TXT copy."

    context = "\n\n".join(
        f"[Handbook passage {idx + 1}]\n{chunk}" for idx, chunk in matches
    )
    prompt = f"""Create exactly {count} distinct multiple-choice {mode} questions for Ontario G1 study.
Selected topic(s): {", ".join(topics)}.
Use ONLY the handbook passages below for factual rules. If the passages do not support enough distinct questions, return only the supported questions rather than inventing facts.
Return ONLY a valid JSON array, with each item having exactly these keys:
{{"id":"unique short string","category":"one selected topic","question":"question text","options":["A...","B...","C...","D..."],"answer":"exactly one option copied verbatim","explanation":"brief explanation supported by the passages"}}
Rules: four distinct options per question; one unambiguous correct answer; answer must exactly match one options entry; explanations must not add unsupported legal facts; do not claim these are actual official exam questions; vary question wording and avoid duplicates.

Handbook passages:
{context}"""
    try:
        response = CLIENT.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=4500,
            system=SYSTEM + "\nWhen generating quizzes, use only the supplied handbook passages and return valid JSON without markdown fences.",
            messages=[{"role": "user", "content": prompt}],
        )
        raw = "\n".join(
            block.text for block in response.content
            if getattr(block, "type", None) == "text"
        ).strip()
        if raw.startswith("```"):
            lines = raw.splitlines()
            if lines:
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            raw = "\n".join(lines).strip()
        questions = json.loads(raw)
        if not isinstance(questions, list):
            raise ValueError("Claude did not return a JSON list.")
        valid = []
        for i, q in enumerate(questions):
            if not isinstance(q, dict):
                continue
            options = q.get("options")
            answer = q.get("answer")
            if not (q.get("question") and isinstance(options, list) and len(options) == 4 and answer in options and q.get("explanation")):
                continue
            category = q.get("category") if q.get("category") in topics else topics[i % len(topics)]
            valid.append({
                "id": f"ai_{int(time.time())}_{i}_{random.randint(1000, 9999)}",
                "category": category,
                "question": str(q["question"]).strip(),
                "options": [str(x) for x in options],
                "answer": str(answer),
                "explanation": str(q["explanation"]).strip(),
            })
        if len(valid) < max(3, min(count, 5)):
            return None, "Claude did not return enough valid questions. Try again or upload a clearer handbook PDF."
        random.shuffle(valid)
        return valid[:count], None
    except json.JSONDecodeError:
        return None, "Claude returned questions in an unexpected format. Please try again."
    except Exception as exc:
        return None, f"Question generation failed: {exc}"


def begin_quiz(mode, questions, timed=False):
    st.session_state.current_test = {
        "mode": mode,
        "questions": questions,
        "answers": {},
        "confidence": {},
        "started": time.time(),
        "timed": timed,
        "submitted": False,
    }
def reset_test():
    st.session_state.current_test = None

if "current_test" not in st.session_state:
    st.session_state.current_test = None
if "tutor_messages" not in st.session_state:
    st.session_state.tutor_messages = []
if "handbook_chunks" not in st.session_state:
    st.session_state.handbook_chunks = []
if "handbook_name" not in st.session_state:
    st.session_state.handbook_name = ""
if "last_result" not in st.session_state:
    st.session_state.last_result = None

# ---------- Sidebar ----------
# with st.sidebar:
#     st.markdown("# 🚗 G1 SmartPrep")
#     st.caption("AI based exam preparation assistant for Ontario G1 exam")
#     page = st.radio("Navigate", [
#         "Home", "Practice Quiz", "Mock Test", "AI Tutor",
#         "Ask the Handbook", "Adaptive Study Plan", "Progress & Research"
#     ])
#     st.divider()
#     st.markdown("**AI status**")
#     st.success("Claude connected") if CLIENT else st.warning("Claude API key not configured")
#     st.markdown(f"[Official MTO Driver’s Handbook ↗]({HANDBOOK_URL})")
#     st.caption("Student project prototype. Verify legal rules with official Ontario sources.")


# ---------- Sidebar ----------
pages = [
    "Home",
    "Practice Quiz",
    "Mock Test",
    "AI Tutor",
    "Ask the Handbook",
    "Adaptive Study Plan",
    "Progress & Research",
]

if "selected_page" not in st.session_state:
    st.session_state["selected_page"] = "Home"

with st.sidebar:
    st.markdown("# 🚗 G1 SmartPrep")
    st.caption("AI based exam preparation assistant for Ontario G1 exam")

    page = st.radio(
        "Navigate",
        pages,
        key="selected_page"
    )

    st.divider()
    st.markdown("**AI status**")

    if CLIENT:
        st.success("Claude connected")
    else:
        st.warning("Claude API key not configured")

    st.markdown(f"[Official MTO Driver’s Handbook ↗]({HANDBOOK_URL})")
    st.caption(
        "Student project prototype. Verify legal rules with official Ontario sources."
    )


# ---------- Home ----------
if page == "Home":
    st.title("Prepare smarter for your Ontario G1")
    st.write("Practice, understand your mistakes, and focus on the topics you need most.")
    c1, c2, c3 = st.columns(3)
    history = get_history()
    total_attempts = len(history)
    all_correct = sum(r["score"] for r in history)
    all_total = sum(r["total"] for r in history)
    accuracy = round(100 * all_correct / all_total) if all_total else 0
    c1.metric("Saved attempts", total_attempts)
    c2.metric("Overall answered accuracy", f"{accuracy}%")
    c3.metric("Available topics", len(TOPICS))
    st.markdown("### Choose your next step")
    a, b, c = st.columns(3)
    with a:
        st.markdown("#### 📝 Practice")
        st.write("Train by topic and review detailed explanations.")
        # if st.button("Open practice quiz", use_container_width=True):
        #     st.session_state.nav_to = "Practice Quiz"
        #     st.rerun()
        if st.button(
            "Open practice quiz",
            use_container_width=True,
            on_click=lambda: st.session_state.update(
                {"selected_page": "Practice Quiz"}
            ),
        ):
            pass
    with b:
        st.markdown("#### ⏱️ Mock test")
        st.write("Try a mixed test without hints during the attempt.")
        # if st.button("Open mock test", use_container_width=True):
        #     st.session_state.nav_to = "Mock Test"
        #     st.rerun()

        if st.button(
            "Open mock test",
            use_container_width=True,
            on_click=lambda: st.session_state.update(
                {"selected_page": "Mock Test"}
            ),
        ):
            pass
    with c:
        st.markdown("#### 🤖 AI tutor")
        st.write("Ask questions and get plain-language explanations.")
        # if st.button("Open AI tutor", use_container_width=True):
        #     st.session_state.nav_to = "AI Tutor"
        #     st.rerun()

        if st.button(
            "Open AI tutor",
            use_container_width=True,
            on_click=lambda: st.session_state.update(
                {"selected_page": "AI Tutor"}
            ),
        ):
            pass

    st.markdown("### Topics available for AI-generated questions")
    st.write(" · ".join(TOPICS))
    st.info("Practice and mock-test questions are generated by Claude from handbook passages you upload. They are practice questions, not actual official MTO exam questions.")
    if not CLIENT:
        st.warning("To enable AI features, create `.streamlit/secrets.toml` and add `ANTHROPIC_API_KEY = \"your-key\"`, then restart the app.")

# ---------- Practice and mock test engine ----------
elif page in ("Practice Quiz", "Mock Test"):
    is_mock = page == "Mock Test"
    st.title("⏱️ Mock Test" if is_mock else "📝 Practice Quiz")
    st.write("Mock test answers and feedback are shown after submission." if is_mock else "Pick topics, answer questions, and review explanations.")
    test = st.session_state.current_test
    if test and ((is_mock and test["mode"] != "Mock Test") or (not is_mock and test["mode"] != "Practice")):
        test = None
    if test is None:
        st.write("Each new attempt generates a fresh question set from the handbook text uploaded for this session.")
        if not st.session_state.handbook_chunks:
            st.warning("Upload the official MTO Driver’s Handbook to generate questions grounded in its content.")
        uploaded_quiz_handbook = st.file_uploader(
            "Upload official MTO Driver’s Handbook (PDF or TXT)",
            type=["pdf", "txt"],
            key=f"quiz_handbook_{page.replace(' ', '_')}"
        )
        if uploaded_quiz_handbook:
            try:
                if uploaded_quiz_handbook.name.lower().endswith(".txt"):
                    handbook_text = uploaded_quiz_handbook.getvalue().decode("utf-8", errors="ignore")
                else:
                    from pypdf import PdfReader
                    import io
                    pdf_reader = PdfReader(io.BytesIO(uploaded_quiz_handbook.getvalue()))
                    handbook_text = "\n".join(p.extract_text() or "" for p in pdf_reader.pages)
                if not handbook_text.strip():
                    st.error("No selectable text was found. Please use a text-based PDF or TXT file.")
                else:
                    st.session_state.handbook_chunks = handbook_chunks(handbook_text)
                    st.session_state.handbook_name = uploaded_quiz_handbook.name
                    st.success(f"Loaded {len(st.session_state.handbook_chunks)} handbook text chunks from {uploaded_quiz_handbook.name}.")
            except Exception as exc:
                st.error(f"Could not read the handbook file: {exc}")
        if st.session_state.handbook_chunks:
            st.caption(f"Source: {st.session_state.handbook_name or 'Uploaded handbook'}")
        with st.container(border=True):
            selected_topics = st.multiselect("Topics", TOPICS, default=[], help="Leave empty to generate a mixed set across topics.")
            count = st.slider("Number of questions", 5, 15 if is_mock else 12, 10)
            timed = st.checkbox("Use a 15-minute timer", value=True) if is_mock else False
            if st.button("Generate fresh questions and start", type="primary", disabled=not bool(st.session_state.handbook_chunks) or not bool(CLIENT)):
                with st.spinner("Claude is generating questions from the handbook..."):
                    generated, error = generate_questions_from_handbook(
                        "Mock Test" if is_mock else "Practice Quiz", selected_topics, count
                    )
                if error:
                    st.error(error)
                else:
                    begin_quiz("Mock Test" if is_mock else "Practice", generated, timed)
                    st.rerun()
    else:
        if test["timed"] and not test["submitted"]:
            render_mock_timer(test["started"])
        if not test["submitted"]:
            with st.form("test_form"):
                new_answers, new_conf = {}, {}
                for idx, q in enumerate(test["questions"], 1):
                    st.markdown(f"**{idx}. {q['question']}**")
                    new_answers[q["id"]] = st.radio(
                        "Select one", q["options"], key=f"{test['mode']}_{q['id']}",
                        index=None, label_visibility="collapsed"
                    )
                    if not is_mock:
                        new_conf[q["id"]] = st.select_slider(
                            "How confident are you?", options=[1,2,3,4,5],
                            value=3, key=f"conf_{q['id']}",
                            help="1 = guessing, 5 = very confident"
                        )
                    else:
                        new_conf[q["id"]] = None
                    st.divider()
                submitted = st.form_submit_button("Submit answers", type="primary")
            if submitted:
                test["answers"] = new_answers
                test["confidence"] = new_conf
                test["submitted"] = True
                score, total, topic_scores, attempt_id = save_attempt(
                    test["mode"], test["questions"], new_answers, new_conf, test["started"]
                )
                st.session_state.last_result = {
                    "mode": test["mode"], "score": score, "total": total,
                    "topic_scores": topic_scores, "attempt_id": attempt_id
                }
                st.rerun()
        else:
            result = st.session_state.last_result
            if result:
                percent = round(100 * result["score"] / result["total"]) if result["total"] else 0
                st.metric("Your score", f"{result['score']} / {result['total']}", f"{percent}%")
                st.progress(percent / 100)
                st.markdown("### Topic breakdown")
                for topic, vals in result["topic_scores"].items():
                    pct = round(100 * vals["correct"] / vals["total"])
                    st.write(f"**{topic}:** {vals['correct']}/{vals['total']} ({pct}%)")
                    st.progress(pct / 100)
                st.markdown("### Review")
                st.caption("Questions were AI-generated from uploaded handbook passages; verify important rules in the current official handbook.")
                for q in test["questions"]:
                    chosen = test["answers"].get(q["id"])
                    ok = chosen == q["answer"]
                    st.markdown(f"{'✅' if ok else '❌'} **{q['question']}**")
                    st.write(f"Your answer: {chosen or 'Not answered'}")
                    st.write(f"Correct answer: {q['answer']}")
                    st.write(q["explanation"])
                    if not ok and not is_mock and st.button(f"Ask AI about this · {q['id']}", key=f"ask_{q['id']}"):
                        st.write(ask_ai(f"Explain this practice question and why the answer is correct.\nQuestion: {q['question']}\nOptions: {q['options']}\nCorrect answer: {q['answer']}\nExplanation: {q['explanation']}\nDirect the learner to the official handbook for exact rules."))
                    st.divider()
                if st.button("Start another test", type="primary"):
                    reset_test()
                    st.session_state.last_result = None
                    st.rerun()
                if st.button("Clear current test"):
                    reset_test()
                    st.rerun()

# ---------- AI Tutor ----------
elif page == "AI Tutor":
    st.title("🤖 AI Driving Tutor")
    st.write("Ask in your own words. The tutor is intended to explain concepts, not replace the handbook.")
    st.warning("AI can make mistakes. Verify exact laws, penalties, and sign meanings in the official handbook.")
    left, right = st.columns([3, 1])
    with right:
        if st.button("Clear conversation"):
            st.session_state.tutor_messages = []
            st.rerun()
        st.markdown(f"[Official handbook ↗]({HANDBOOK_URL})")
    for msg in st.session_state.tutor_messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
    prompt = st.chat_input("Ask about road signs, right of way, or safe driving…")
    if prompt:
        st.session_state.tutor_messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)
        history_text = "\n".join(f"{m['role']}: {m['content']}" for m in st.session_state.tutor_messages[-8:])
        response = ask_ai(history_text)
        st.session_state.tutor_messages.append({"role": "assistant", "content": response})
        with st.chat_message("assistant"):
            st.markdown(response)
            st.caption("Verify exact rules in the Official MTO Driver’s Handbook.")

# ---------- Handbook assistant ----------
elif page == "Ask the Handbook":
    st.title("📚 Ask the Handbook")
    st.write("Upload the official handbook as a text-based PDF or TXT. The app retrieves relevant passages for answers and for generating fresh quiz questions.")
    st.markdown(f"**Official source:** [Ontario Driver’s Handbook]({HANDBOOK_URL})")
    st.warning("Only use an official, current copy. If the PDF is scanned or image-only, text extraction may not work. The upload is processed locally for this session.")
    uploaded = st.file_uploader("Upload handbook or study notes", type=["pdf", "txt"])
    if uploaded:
        raw_text = ""
        try:
            if uploaded.name.lower().endswith(".txt"):
                raw_text = uploaded.getvalue().decode("utf-8", errors="ignore")
            else:
                from pypdf import PdfReader
                import io
                reader = PdfReader(io.BytesIO(uploaded.getvalue()))
                raw_text = "\n".join(page.extract_text() or "" for page in reader.pages)
            chunks = handbook_chunks(raw_text)
            st.session_state.handbook_chunks = chunks
            st.session_state.handbook_name = uploaded.name
            st.success(f"Loaded {len(chunks)} text chunks from {uploaded.name}.")
            if not raw_text.strip():
                st.error("No selectable text was found. Try a text-based PDF or TXT file.")
        except Exception as e:
            st.error(f"Could not read this file: {e}")
    if st.session_state.handbook_chunks:
        st.caption(f"Current source: {st.session_state.handbook_name}")
        query = st.text_input("Your handbook question", placeholder="What does this handbook say about approaching an intersection?")
        if st.button("Search and answer", type="primary", disabled=not query.strip()):
            matches = retrieve_handbook(query, st.session_state.handbook_chunks)
            if not matches:
                st.warning("No strong text match found. Try different keywords or check the handbook directly.")
            else:
                context = "\n\n".join(f"[Retrieved passage {idx+1}]\n{chunk}" for idx, chunk in matches)
                prompt = f"""Answer the learner's question using ONLY the retrieved passages below. If the passages do not contain the answer, say that the uploaded text does not provide enough information. Do not fill gaps from memory. Cite passage labels such as [Retrieved passage 2]. Keep exact legal claims cautious.

Question: {query}

Retrieved passages:
{context}"""
                answer = ask_ai(prompt, system="You are a source-grounded study assistant. Answer only from the supplied passages. Do not invent rules or cite sources that were not supplied.")
                st.markdown("### Answer")
                st.markdown(answer)
                with st.expander("View retrieved source passages"):
                    for idx, chunk in matches:
                        st.markdown(f"**Retrieved passage {idx+1}**")
                        st.write(chunk)
    else:
        st.info("Upload the official handbook PDF or text notes to enable passage retrieval. Without an uploaded source, this page will not guess an answer.")

# ---------- Adaptive study plan ----------
elif page == "Adaptive Study Plan":
    st.title("🧭 Adaptive Study Plan")
    history = get_history()
    responses = get_responses()
    if not history:
        st.info("Complete a practice quiz or mock test first. Your saved results will help prioritize topics.")
    else:
        topic_data = {}
        for r in responses:
            x = topic_data.setdefault(r["topic"], {"correct": 0, "total": 0})
            x["total"] += 1
            x["correct"] += int(r["is_correct"])
        ranked = sorted(topic_data.items(), key=lambda x: x[1]["correct"] / max(1, x[1]["total"]))
        st.markdown("### Recommended priorities")
        for topic, values in ranked:
            acc = round(values["correct"] / values["total"] * 100)
            st.write(f"**{topic}** — {acc}% accuracy across {values['total']} answered questions")
            st.progress(acc / 100)
        weak = [topic for topic, values in ranked if values["correct"] / max(1, values["total"]) < 0.8]
        
        if not weak:
            weak = [ranked[0][0]]
        st.markdown("### Generate a personalized 7-day plan")
        daily = st.selectbox("Time per day", ["15 minutes", "30 minutes", "45 minutes", "60 minutes"])
        current_level = st.selectbox("Current preparation", ["Beginner", "Developing", "Near test-ready"])
        
        if st.button("Generate plan", type="primary"):
            prompt = f"""Create a realistic 7-day Ontario G1 study plan.
Preparation level: {current_level}. Daily time: {daily}.
Prioritize these measured weak topics: {weak}.
Suggest active recall, mixed practice, and reviewing mistakes. Do not invent exact legal requirements;
direct the learner to verify rules in the Official MTO Driver's Handbook. Keep it practical and structured by day."""
            st.markdown(ask_ai(prompt))





# ---------- Progress & research ----------
elif page == "Progress & Research":
    st.title("📈 Progress & Research Evaluation")
    history = get_history()
    responses = get_responses()
    if not history:
        st.info("Your saved quiz results will appear here after your first attempt.")
    else:
        import pandas as pd
        df = pd.DataFrame([dict(r) for r in reversed(history)])
        df["accuracy"] = (df["score"] / df["total"] * 100).round(1)
        a, b, c = st.columns(3)
        a.metric("Attempts", len(df))
        b.metric("Latest accuracy", f"{df.iloc[-1]['accuracy']}%")
        c.metric("Best accuracy", f"{df['accuracy'].max()}%")
        st.markdown("### Accuracy over time")
        st.line_chart(df.set_index("created_at")["accuracy"])
        st.markdown("### Attempt history")
        st.dataframe(df[["created_at", "mode", "score", "total", "accuracy", "duration_seconds", "confidence"]], use_container_width=True, hide_index=True)
        st.download_button(
            "Download attempt history (CSV)",
            df.to_csv(index=False).encode("utf-8"),
            file_name="g1_smartprep_attempts.csv",
            mime="text/csv"
        )
        # st.markdown("### Research-friendly interpretation")
        # st.write("Use these session records to inspect performance trends. A single learner's app history does not establish that AI caused an improvement.")
        # st.write("For a stronger study design, compare groups using the same content and study time: guided AI tutor, unrestricted AI, and conventional study. Include a pre-test, unassisted post-test, and a delayed retention test (for example, after 7 days).")
        # st.write("Measure unassisted accuracy, retention, confidence calibration, and perceived usefulness. Obtain appropriate instructor/ethics guidance before collecting data from participants.")
        
        if st.button("Delete all saved quiz history", type="secondary"):
            st.session_state.confirm_delete = True
        
        if st.session_state.get("confirm_delete"):
            st.warning("This permanently deletes local quiz history from this app's SQLite database.")
            yes, no = st.columns(2)
            
            if yes.button("Confirm delete"):
                with db() as conn:
                    conn.execute("DELETE FROM responses")
                    conn.execute("DELETE FROM attempts")
                st.session_state.confirm_delete = False
                st.success("Local history cleared.")
                st.rerun()
            
            if no.button("Cancel"):
                st.session_state.confirm_delete = False
                st.rerun()

st.divider()
st.caption("G1 SmartPrep is an educational prototype, not an official Ontario government service. Always verify exact rules in the current Official MTO Driver’s Handbook.")

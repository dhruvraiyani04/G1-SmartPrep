# G1 SmartPrep — Ontario G1 Test Preparation

G1 SmartPrep is a student-developed Streamlit application designed to support preparation for the Ontario G1 knowledge test. It combines AI-generated practice questions based on an uploaded handbook, a conversational AI tutor, handbook passage search, adaptive study recommendations, and local progress tracking.

This repository is the project version presented in class. The application source code (`app.py`) should remain the same as the version presented.

> **Disclaimer:** G1 SmartPrep is an independent educational prototype. It is not an official Government of Ontario or Ministry of Transportation of Ontario (MTO) service. Practice questions are not actual or guaranteed MTO exam questions. Always verify driving rules and legal requirements using the current official MTO Driver's Handbook.

## Features

- **Practice Quiz:** Generate fresh multiple-choice questions from handbook passages uploaded during the current session. Choose topics and the number of questions, record confidence, and review explanations after submission.
- **Mock Test:** Generate a mixed practice test from the uploaded handbook, with an optional 15-minute timer.
- **AI Driving Tutor:** Ask driving-related questions and receive conversational explanations powered by Anthropic Claude.
- **Ask the Handbook:** Search relevant passages from an uploaded PDF or TXT file and ask questions using the retrieved text as context.
- **Adaptive Study Plan:** Review topic-level performance from saved answers and request a 7-day study plan based on weaker topics.
- **Progress & Research Evaluation:** View saved attempt history, accuracy charts and performance summaries; export attempt history as CSV; delete locally saved quiz history.
- **SQLite storage:** Save assessment summaries and individual question responses locally.

## Technology Stack

- Python 3.10+
- Streamlit
- Anthropic Python SDK (Claude Haiku 4.5)
- SQLite (Python standard library)
- pypdf (PDF text extraction)
- pandas (history table and CSV export)

## Requirements

- Python 3.10 or newer
- An Anthropic API key for AI features
- Internet connection for requests to the Anthropic API

The application can start without a configured API key, but Claude-powered features will not be available.

## Installation and Setup

### 1. Download the repository

Clone the repository:

```bash
git clone YOUR_GITHUB_REPOSITORY_URL
cd G1-SmartPrep
```

Or download the repository as a ZIP file and extract it.

### 2. (Recommended) Create a virtual environment

**Windows PowerShell:**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 3. Install dependencies

```bash
python -m pip install -r requirements.txt
```

### 4. Configure your Anthropic API key

Create a file named `secrets.toml` inside the `.streamlit` folder:

```text
.streamlit/secrets.toml
```

Add your own Anthropic API key:

```toml
ANTHROPIC_API_KEY = "YOUR_ANTHROPIC_API_KEY"
```

Do not commit or share this file. The `.gitignore` file excludes it. The repository includes `.streamlit/secrets.toml.example` as a safe template.

### 5. Run the application

From the project root folder:

```bash
python -m streamlit run app.py
```

Open the local URL displayed in the terminal.

## Uploading the Driver's Handbook

The Practice Quiz, Mock Test, and Ask the Handbook features use text extracted from a PDF or TXT file uploaded by the user.

- The app does not automatically download the MTO handbook.
- Upload a current, text-based copy of the Official MTO Driver's Handbook.
- Uploaded text is held in Streamlit session state; you may need to upload the handbook again in a new session.
- Scanned or image-only PDFs may not provide extractable text.

Official handbook: https://www.ontario.ca/document/official-mto-drivers-handbook

## How Handbook Retrieval Works

The application splits extracted handbook text into overlapping chunks and uses basic keyword matching to retrieve relevant passages. This is a lightweight lexical retrieval approach, not embedding-based semantic search. It may miss relevant information when the learner's wording differs from the handbook's wording.

For question generation, Claude receives retrieved handbook passages and is instructed to base generated questions on those passages. The application validates the returned JSON structure and basic question fields, but AI-generated content should still be checked against the official handbook.

The **AI Driving Tutor** is a separate conversational feature. It sends the conversation to Claude and does not automatically retrieve passages from the uploaded handbook. Use **Ask the Handbook** when you want an answer based on retrieved uploaded text.

## Local Database

The app creates its SQLite database automatically at:

```text
data/g1_smartprep.db
```

It stores:
- Assessment attempt summaries, including date, mode, score, topic scores, duration and confidence.
- Individual question responses, including selected answer, correct answer and correctness.

The database is stored locally on the machine running the app. It is excluded from Git by `.gitignore`; do not publish personal or participant data.

## Limitations

- Claude can generate inaccurate or incomplete explanations. Verify exact legal rules, penalties, signs and restrictions using the current official handbook.
- Practice questions are AI-generated study material, not official MTO exam questions.
- Keyword retrieval is less capable than semantic or embedding-based retrieval and may not find every relevant passage.
- Scanned PDFs may not yield selectable text.
- Uploaded handbook text is session-based and is not intended as permanent document storage.
- The mock-test timer displays the remaining time and warns when time is up; it does not automatically submit the test.
- The adaptive plan is an AI-generated study suggestion, not a validated educational intervention.

## Research Context

The application includes saved performance data that may be useful when exploring AI-supported exam preparation. However, a learner's quiz history alone cannot establish that AI caused an improvement in learning. Any formal study involving participants should follow instructor and institutional research-ethics guidance.


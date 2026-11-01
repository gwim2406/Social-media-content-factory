# Social-media-content-factory

An AI-assisted, human-supervised social media content production workflow. It researches topics using signed-in browser-based AI sessions, consolidates ideas for human approval, and can write scripts for approved ideas. A local dashboard and command-line interface manage and review workflow tasks.

## Initial setup

The project requires Python 3.10 or later.

1. Create and activate a virtual environment from the project directory:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

2. Install the Python dependencies:

   ```bash
   python -m pip install -r requirements.txt
   ```

3. Install the Playwright Chromium browser used by browser-based workflows:

   ```bash
   python -m playwright install chromium
   ```

4. Start the local dashboard:

   ```bash
   python -m orchestration.cli dashboard
   ```

   Open [http://127.0.0.1:8765](http://127.0.0.1:8765) in your browser. The dashboard binds to localhost by default.
# Sample workspace

A small Python app the Strands IDE agent can read, edit, and run. It is a
Flask web service exposing a few endpoints, with pytest tests.

Try asking the agent things like:

- "Add a /health endpoint that returns JSON with status ok and the current time."
- "Add a /items endpoint that returns a list, and a POST /items that adds one."
- "Write tests for the new endpoints and run pytest."
- "The divide endpoint crashes on zero — fix it and add a test."

Run it yourself:

    python app.py            # serves http://localhost:5000
    pytest -q                # run tests

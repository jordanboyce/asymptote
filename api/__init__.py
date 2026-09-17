"""HTTP API for Clio, organized as one router module per domain.

Routers keep their full URL paths (no prefixes) so the public API is
unchanged; `main.py` assembles them into the FastAPI app.
"""

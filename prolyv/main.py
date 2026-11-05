"""uvicorn entry point: `uvicorn prolyv.main:app`."""

from prolyv.app import create_app

app = create_app()

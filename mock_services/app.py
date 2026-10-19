"""Local mock bank/lender service boundary.

Stands in for external bank and lender APIs during the competition build. It holds no live
credentials and moves no money. Later feature prompts add individual mock adapters behind it;
for now it exposes only a health endpoint so the API can verify the boundary is reachable.

Run (from repo root):  backend/.venv/Scripts/python -m uvicorn mock_services.app:app --port 8100
"""

from datetime import datetime, timezone

from fastapi import FastAPI

app = FastAPI(title="SME Finance Copilot - mock external services", version="0.0.1")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "mode": "sandbox", "checked_at": datetime.now(timezone.utc).isoformat()}

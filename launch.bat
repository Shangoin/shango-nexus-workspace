@echo off
echo.
echo  ██████████████████████████████████████████
echo  ██  SHANGO NEXUS — Alien Intelligence HQ  ██
echo  ██████████████████████████████████████████
echo.

:: Check .env exists
if not exist ".env" (
  echo [!] .env not found. Copying from .env.example...
  copy .env.example .env
  echo [!] Edit .env with your API keys before continuing.
  pause
  exit /b 1
)

echo [1/4] Starting Redis via Docker...
docker run -d --name nexus-redis -p 6379:6379 redis:7-alpine 2>nul || echo Redis already running

echo [2/4] Starting Nexus Backend...
start cmd /k "cd nexus-backend && (if not exist .venv python -m venv .venv) && .venv\Scripts\activate && pip install -r requirements.txt -q && uvicorn main:app --reload --port 8000"

echo [3/4] Starting Streamlit Dashboard...
start cmd /k "cd nexus-dashboard && pip install streamlit plotly pandas httpx -q && streamlit run dashboard.py --server.port 8501"

echo [4/4] Starting Landing Page...
start cmd /k "cd landing && npm install -q && npm run dev"

echo.
echo  All services starting...
echo  Backend:    http://localhost:8000/health
echo  API Docs:   http://localhost:8000/docs  
echo  Dashboard:  http://localhost:8501
echo  Landing:    http://localhost:3000
echo  Nexus KPIs: http://localhost:3000/nexus
echo.
timeout /t 5
start http://localhost:8000/health

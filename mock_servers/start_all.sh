#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Setup venv if it doesn't exist
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi

source .venv/bin/activate

# Install dependencies if needed
pip install -q -r requirements.txt 2>/dev/null

echo ""
echo "=== Starting Mock Cloud API Servers ==="
echo ""
echo "  Crusoe  → http://localhost:8001       (REST, API key: crusoe-test-key-001)"
echo "  Lambda  → http://localhost:8002       (REST, API key: lambda-test-key-001)"
echo "  Nebius  → grpc://localhost:50051      (gRPC, API key: nebius-test-key-001)"
echo ""

# Start servers in background
python crusoe_server.py &
CRUSOE_PID=$!

python lambda_server.py &
LAMBDA_PID=$!

python nebius_server.py &
NEBIUS_PID=$!

echo "Servers started (PIDs: Crusoe=$CRUSOE_PID, Lambda=$LAMBDA_PID, Nebius=$NEBIUS_PID)"
echo "Press Ctrl+C to stop all servers."

# Cleanup on exit
trap "kill $CRUSOE_PID $LAMBDA_PID $NEBIUS_PID 2>/dev/null; echo 'Servers stopped.'" EXIT

wait

tell application "Terminal"
    do script "cd /Users/jw085395/cards/backend/poker-backend && source ./.venv/bin/activate && python manage.py runserver"
    do script "cd /Users/jw085395/cards/engine && go run *.go"
    do script "cd /Users/jw085395/cards/backend/poker-backend && source ./.venv/bin/activate && pytest -s ./poker/test_websockets.py"
end tell
# osascript /Users/jw085395/cards/backend/run.scpt
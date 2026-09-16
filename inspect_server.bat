@echo off
title Modish - server inspection (read-only)
setlocal
set SERVER=root@194.87.118.236

echo.
echo  Inspecting the server (READ-ONLY, nothing is changed).
echo  Enter the server password when asked (one time).
echo.

ssh -o StrictHostKeyChecking=accept-new %SERVER% "echo '=== /opt ==='; ls -la /opt 2>/dev/null; echo; echo '=== project candidates ==='; for d in /opt/modish /opt/Modish /root/modish /srv/modish; do [ -d \"$d\" ] && echo \"FOUND: $d\" && ls \"$d\" && [ -d \"$d/backend\" ] && ls \"$d/backend\" | head -20; done; echo; echo '=== docker ==='; docker ps -a --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null; echo; echo '=== compose files ==='; find /opt /root /srv -maxdepth 3 -name 'docker-compose*.yml' 2>/dev/null; echo; echo '=== env keys (names only, no secrets) ==='; for f in /opt/modish/backend/.env /opt/Modish/backend/.env; do [ -f \"$f\" ] && echo \"$f:\" && grep -oE '^[A-Za-z_]+' \"$f\"; done; echo; echo '=== health ==='; curl -s --max-time 5 http://127.0.0.1:8000/health || curl -s --max-time 5 http://127.0.0.1/health; echo; echo '=== nginx ==='; ls /etc/nginx/sites-enabled/ 2>/dev/null; echo; echo '=== disk ==='; df -h / | tail -1" > "%~dp0server_info.txt" 2>&1

if errorlevel 1 (
  echo.
  echo  ERROR: could not connect. See server_info.txt for details.
) else (
  echo.
  echo  DONE. Report saved to server_info.txt in the modish folder.
)
echo.
pause

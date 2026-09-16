@echo off
title Modish - deep diagnostics (read-only)
setlocal
set SERVER=root@194.87.118.236

echo.
echo  Collecting diagnostics (READ-ONLY). One password prompt.
echo.

ssh -o StrictHostKeyChecking=accept-new %SERVER% "echo '=== backend/scripts ==='; ls -la /opt/Modish/backend/scripts/ 2>&1; echo; echo '=== entrypoint.sh content ==='; cat /opt/Modish/backend/scripts/entrypoint.sh 2>&1; echo; echo '=== entrypoint.sh first bytes (hex) ==='; head -c 80 /opt/Modish/backend/scripts/entrypoint.sh 2>/dev/null | od -c | head -6; echo; echo '=== root docker-compose.yml ==='; cat /opt/Modish/docker-compose.yml 2>&1; echo; echo '=== backend Dockerfile ==='; cat /opt/Modish/backend/Dockerfile 2>&1; echo; echo '=== container config ==='; docker inspect modish_backend --format 'Cmd={{.Config.Cmd}} Entrypoint={{.Config.Entrypoint}} WorkDir={{.Config.WorkingDir}}' 2>&1; echo '=== mounts ==='; docker inspect modish_backend --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}' 2>&1; echo '=== image file check ==='; docker run --rm --entrypoint ls modish-backend -la /app/scripts 2>&1 | head -10" > "%~dp0server_diag.txt" 2>&1

echo  DONE. Report saved to server_diag.txt in the modish folder.
echo.
pause

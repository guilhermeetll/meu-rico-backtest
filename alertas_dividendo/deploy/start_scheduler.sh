#!/bin/bash
# Sobe o scheduler se ele ainda não estiver rodando.
# Pasta do bot: ALERTAS_BASE_DIR (padrão /workspace/alertas-dividendo).
export ALERTAS_BASE_DIR="${ALERTAS_BASE_DIR:-/workspace/alertas-dividendo}"
pgrep -f "$ALERTAS_BASE_DIR/scheduler.py" >/dev/null || setsid nohup python3 "$ALERTAS_BASE_DIR/scheduler.py" >> "$ALERTAS_BASE_DIR/scheduler.log" 2>&1 < /dev/null &

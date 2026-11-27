#!/bin/bash
# uso: run.sh run|exit-reminder|entry-reminder
# Pasta do bot: ALERTAS_BASE_DIR (padrão /workspace/alertas-dividendo).
BASE="${ALERTAS_BASE_DIR:-/workspace/alertas-dividendo}"
set -a
. "$BASE/token.env"
. "$BASE/chat_id.env"
set +a
export ALERTAS_DATA_DIR="$BASE/data" ALERTAS_SIMULACAO=1 TZ=America/Sao_Paulo
VENV="$BASE/venv-alertas"
APP="$BASE/app/alertas_dividendo"
LOG="$BASE/cron.log"
LOCK="$BASE/.lock"

# Apaga só o venv incompleto deste bot. Não mexe em outro caminho.
discard_venv() {
  case "${VENV##*/}" in
    venv-alertas) ;;
    *)
      echo "$(date '+%F %T') não apaguei $VENV (nome inesperado)" >> "$LOG"
      return
      ;;
  esac
  if [ -e "$VENV" ]; then
    rm -rf -- "$VENV"
    echo "$(date '+%F %T') venv incompleto removido: $VENV" >> "$LOG"
  fi
}

# Mesmo lock de antes (flock -w 1200). O rebuild fica aqui dentro para dois
# jobs não criarem o venv ao mesmo tempo.
exec 9>"$LOCK"
if ! /usr/bin/flock -w 1200 9; then
  echo "$(date '+%F %T') não obtive o lock em 1200s ($LOCK); abortando" >> "$LOG"
  exit 1
fi

# A troca do computador apaga pastas com ponto (.venv). O venv sem ponto
# sobrevive; se ainda assim sumir, recria a partir do requirements.txt.
if [ ! -x "$VENV/bin/python" ]; then
  echo "$(date '+%F %T') venv ausente, recriando" >> "$LOG"
  if ! python3 -m venv "$VENV" >> "$LOG" 2>&1; then
    echo "$(date '+%F %T') falha ao criar o venv em $VENV; abortando" >> "$LOG"
    discard_venv
    exit 1
  fi
  if ! "$VENV/bin/pip" install -q -r "$APP/requirements.txt" >> "$LOG" 2>&1; then
    echo "$(date '+%F %T') falha ao instalar $APP/requirements.txt; abortando sem rodar o python" >> "$LOG"
    discard_venv
    exit 1
  fi
fi

if [ ! -x "$VENV/bin/python" ]; then
  echo "$(date '+%F %T') python do venv ausente em $VENV/bin/python; abortando" >> "$LOG"
  exit 1
fi

cd "$APP" || {
  echo "$(date '+%F %T') pasta do app ausente: $APP; abortando" >> "$LOG"
  exit 1
}

exec >> "$LOG" 2>&1
exec "$VENV/bin/python" -m alertas_dividendo "$@"

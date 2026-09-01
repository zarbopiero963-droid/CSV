#!/usr/bin/env sh
# Servizio cron d'esempio: chiama l'endpoint di invio backup del relay.
# NON contiene segreti: legge tutto da variabili d'ambiente impostate su Railway.
#
# Variabili richieste (sul servizio cron, Railway -> Variables):
#   BACKUP_BASE_URL     es. https://betrelay.net  (uno slash finale non da' fastidio)
#   BACKUP_CRON_TOKEN   lo STESSO valore impostato sul servizio principale del relay
set -eu

: "${BACKUP_BASE_URL:?BACKUP_BASE_URL non impostata}"
: "${BACKUP_CRON_TOKEN:?BACKUP_CRON_TOKEN non impostata}"

# -f  : esce non-zero su HTTP >= 400, cosi' un 404/400 fa fallire il job e si vede nei log
# -sS : silenzioso ma mostra gli errori
curl -fsS -X POST "${BACKUP_BASE_URL%/}/api/admin/backup/invia" \
     -H "X-Backup-Cron-Token: ${BACKUP_CRON_TOKEN}"

# Servizio cron: invio notturno del backup

Il relay non ha uno scheduler interno. Questo servizio chiama, su schedule,
l'endpoint `POST /api/admin/backup/invia`, che copia il DB e lo manda al canale
privato configurato dal pannello admin.

## Setup su Railway (una volta)

1. Nuovo servizio dallo stesso repo (o "empty service" con il comando sotto).
2. Start / Cron command: `sh deploy/cron-backup/invia_backup.sh`
3. Cron schedule: es. `0 2 * * *` (02:00 UTC — attenzione al fuso: e' ~le 03:00/04:00
   in Italia).
4. Variables del servizio cron:
   - `BACKUP_BASE_URL` = URL pubblico del relay, es. `https://betrelay.net`
   - `BACKUP_CRON_TOKEN` = lo STESSO valore impostato sul servizio principale.

## Prerequisiti sul servizio principale

- `BACKUP_CRON_TOKEN` impostata (stesso valore). Se vuota, l'endpoint risponde
  404 al cron (fail-closed): nessun invio automatico.
- Canale di backup configurato e confermato dal pannello admin.
- Bot amministratore del canale, con permesso di postare documenti.

## Il token non sta qui

Nessun segreto in questi file: `BACKUP_CRON_TOKEN` vive solo nelle Variables
di Railway, mai nel repo, mai nei log. Lo script passa il token nell'header
`X-Backup-Cron-Token`, mai nell'URL (l'URL finisce nei log).

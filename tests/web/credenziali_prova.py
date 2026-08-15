"""Le credenziali del login a password nei test browser — fonte unica.

Le leggono in due: la fixture di `test_prototype_flow.py`, che ne mette l'HASH
nell'ambiente del relay (`ADMIN_PASSWORD_HASH`), e gli script Playwright, che
digitano la password nella pagina. Due copie divergerebbero al primo cambio, e
il sintomo sarebbe un login «sbagliato» in un test che non sta provando il
fallimento. Valgono SOLO nei test: nessun servizio reale conosce questa coppia.
"""

UTENTE_PROVA = 'administrator'
PASSWORD_PROVA = 'la-password-del-prototipo'

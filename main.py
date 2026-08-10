import csv, io, os, re, sqlite3
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel

app = FastAPI(title='XTrader Signal Relay')

@app.on_event('startup')
async def register_telegram_webhook():
    token = os.getenv('TELEGRAM_BOT_TOKEN', '')
    public_url = os.getenv('PUBLIC_URL', 'https://csv-production-b04e.up.railway.app')
    if token:
        import urllib.request
        url = f'https://api.telegram.org/bot{token}/setWebhook?url={public_url}/telegram/webhook'
        try:
            urllib.request.urlopen(url, timeout=10).read()
        except Exception:
            pass
DB_PATH = os.getenv('DB_PATH', '/tmp/signals.db')
TOKEN = os.getenv('CSV_ACCESS_TOKEN', '')
HEADERS = ['Provider','EventId','EventName','MarketId','MarketName','MarketType','SelectionId','SelectionName','Handicap','Price','MinPrice','MaxPrice','BetType','Points']

class MessageIn(BaseModel): message: str
class ParserIn(BaseModel):
    name: str
    header: str
    market_name: str = 'Over/Under 1,5 gol'
    market_type: str = 'OVER_UNDER_15'
    selection_name: str = 'Over 1,5 goal'
    handicap: str = '0'
    bet_type: str = 'PUNTA'


def db():
    c=sqlite3.connect(DB_PATH)
    c.execute('CREATE TABLE IF NOT EXISTS signals (id INTEGER PRIMARY KEY AUTOINCREMENT, csv TEXT NOT NULL, parser TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP, expires_at INTEGER)')
    try: c.execute('ALTER TABLE signals ADD COLUMN expires_at INTEGER')
    except sqlite3.OperationalError: pass
    c.execute('CREATE TABLE IF NOT EXISTS parsers (name TEXT PRIMARY KEY, header TEXT NOT NULL, market_name TEXT, market_type TEXT, selection_name TEXT, handicap TEXT, bet_type TEXT)')
    c.execute("INSERT OR IGNORE INTO parsers VALUES (?,?,?,?,?,?,?)", ('Parser_Telegram_XTrader_v1','P.Bet. PREMACHT 0,5HT','Over/Under 1,5 gol','OVER_UNDER_15','Over 1,5 goal','0','PUNTA'))
    c.commit(); return c

def make_csv(row):
    out=io.StringIO(newline=''); csv.writer(out,quoting=csv.QUOTE_ALL,lineterminator='\r\n').writerows([HEADERS,row]); return out.getvalue()

def store_signal(c, csv_text, parser):
    # Un messaggio produce una sola riga. Il messaggio successivo sostituisce il precedente.
    c.execute('DELETE FROM signals')
    c.execute('INSERT INTO signals(csv,parser,expires_at) VALUES (?,?,?)',(csv_text,parser,int(__import__('time').time())+90))

def parse_message(message, cfg):
    if cfg['header'].lower() not in message.lower(): return None
    line=next((x.strip() for x in message.splitlines() if '🆚' in x), '')
    if not line: return None
    event=line.split('🆚',1)[1].strip(); event=event.splitlines()[0].strip()
    # L'ultimo " v " è il separatore; eventuali v precedenti restano nel nome squadra.
    ms=list(re.finditer(r'\s+v\s+',event,flags=re.I))
    if ms:
        s=ms[-1]; event=event[:s.start()].strip()+' - '+event[s.end():].strip()
    row=['XTrader','',event,'',cfg['market_name'],cfg['market_type'],'',cfg['selection_name'],cfg['handicap'],'','','',cfg['bet_type'],'']
    return {'event':event,'csv':make_csv(row)}

def auth(token):
    if TOKEN and token != TOKEN: raise HTTPException(401,'Unauthorized')

def get_parser(c,name):
    r=c.execute('SELECT name,header,market_name,market_type,selection_name,handicap,bet_type FROM parsers WHERE name=?',(name,)).fetchone()
    if not r: raise HTTPException(404,'Parser non trovato')
    return dict(zip(['name','header','market_name','market_type','selection_name','handicap','bet_type'],r))

@app.get('/')
def root(): return {'service':'xtrader-signal-relay','status':'online','csv':'/xtrader.csv'}
@app.get('/health')
def health(): return {'status':'ok'}
@app.get('/xtrader.csv')
def xtrader_csv(token: str|None=Query(None)):
    auth(token); c=db(); c.execute('DELETE FROM signals WHERE expires_at IS NOT NULL AND expires_at <= strftime(\'%s\',\'now\')'); c.commit(); r=c.execute('SELECT csv FROM signals ORDER BY id DESC LIMIT 1').fetchone(); c.close()
    empty=io.StringIO(newline=''); csv.writer(empty,quoting=csv.QUOTE_ALL,lineterminator='\r\n').writerow(HEADERS)
    return Response(r[0] if r else empty.getvalue(),media_type='text/csv',headers={'Cache-Control':'no-store'})

@app.get('/api/parsers')
def list_parsers(x_admin_token: str|None=Header(None)):
    auth(x_admin_token); c=db(); rows=c.execute('SELECT name,header,market_name,market_type,selection_name,handicap,bet_type FROM parsers ORDER BY name').fetchall(); c.close()
    keys=['name','header','market_name','market_type','selection_name','handicap','bet_type']; return [dict(zip(keys,r)) for r in rows]

@app.post('/api/parsers')
def save_parser(data: ParserIn, x_admin_token: str|None=Header(None)):
    auth(x_admin_token); c=db(); c.execute('INSERT OR REPLACE INTO parsers VALUES (?,?,?,?,?,?,?)',tuple(data.model_dump().values())); c.commit(); c.close(); return {'ok':True,'parser':data.name}

@app.delete('/api/parsers/{name}')
def delete_parser(name:str,x_admin_token:str|None=Header(None)):
    auth(x_admin_token); c=db(); c.execute('DELETE FROM parsers WHERE name=?',(name,)); c.commit(); c.close(); return {'ok':True}

@app.post('/api/parsers/{name}/test')
def test_parser(name:str,data:MessageIn,x_admin_token:str|None=Header(None)):
    auth(x_admin_token); c=db(); cfg=get_parser(c,name); parsed=parse_message(data.message,cfg)
    if not parsed: raise HTTPException(422,'Messaggio non riconosciuto da questo parser')
    store_signal(c,parsed['csv'],name); c.commit(); c.close(); return {'ok':True,'parser':name,'event':parsed['event'],'csv':parsed['csv']}

@app.post('/api/test-message')
def test_message(data:MessageIn, x_admin_token:str|None=Header(None), parser: str=Query('Parser_Telegram_XTrader_v1')):
    return test_parser(parser,data,x_admin_token)

@app.post('/telegram/webhook')
async def telegram_webhook(request: Request):
    payload = await request.json()
    msg = payload.get('message') or payload.get('channel_post') or {}
    chat = msg.get('chat') or {}
    allowed = {x.strip() for x in os.getenv('TELEGRAM_ALLOWED_CHAT_IDS','').split(',') if x.strip()}
    if allowed and str(chat.get('id')) not in allowed:
        return {'ok': True, 'ignored': 'chat_not_allowed'}
    text = msg.get('text') or msg.get('caption') or ''
    if not text:
        return {'ok': True, 'ignored': 'no_text'}
    c=db(); cfg=get_parser(c,'Parser_Telegram_XTrader_v1'); parsed=parse_message(text,cfg)
    if not parsed:
        c.close(); return {'ok': True, 'ignored': 'parser_no_match'}
    store_signal(c,parsed['csv'],'Parser_Telegram_XTrader_v1'); c.commit(); c.close()
    return {'ok': True, 'event': parsed['event']}

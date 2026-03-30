import os, json, threading, time
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)
sessions = {}

TOKEN = os.getenv('WAPI_TOKEN', '')
NUMBER = os.getenv('WAPI_NUMBER', '')
GROUP  = os.getenv('GROUP_ID', '120363039812918773@g.us')
FOLDER = os.getenv('FOLDER_ID', '1uxLpoZ_oGYAymVBJzA60SH_ZVipFs3y5')
PORTAL = os.getenv('PORTAL_URL', 'https://wapiportal-fvw6onlv.manus.space')
PORTAL_USER = os.getenv('PORTAL_USER', 'BRUNO')
PORTAL_PASS = os.getenv('PORTAL_PASS', '373341')

print(f"[OK] TOKEN={'sim' if TOKEN else 'NAO'} | NUMBER={'sim' if NUMBER else 'NAO'}")

_portal_session = requests.Session()
_portal_logged_in = False
_last_login_attempt = 0

def portal_login():
    global _portal_logged_in, _last_login_attempt
    # Evita tentar relogar mais de uma vez por minuto
    now = time.time()
    if now - _last_login_attempt < 60:
        print(f"[PORTAL] Aguardando cooldown de login ({int(60-(now-_last_login_attempt))}s)")
        return _portal_logged_in
    _last_login_attempt = now
    try:
        resp = _portal_session.post(
            f"{PORTAL}/api/auth/login",
            json={"username": PORTAL_USER, "password": PORTAL_PASS},
            headers={"Content-Type": "application/json"},
            timeout=15
        )
        if resp.status_code == 200:
            data = resp.json()
            print(f"[PORTAL] Login OK: {data.get('name', '?')}")
            _portal_logged_in = True
            return True
        else:
            print(f"[PORTAL] Login falhou: {resp.status_code} {resp.text[:120]}")
            _portal_logged_in = False
            return False
    except Exception as e:
        print(f"[PORTAL] Erro no login: {e}")
        _portal_logged_in = False
        return False

def enviar(para, texto):
    global _portal_logged_in
    try:
        if not _portal_logged_in:
            if not portal_login():
                print("[ERRO] Nao foi possivel logar no portal")
                return
        payload = {"0": {"json": {"to": para, "body": texto}}}
        resp = _portal_session.post(
            f"{PORTAL}/api/trpc/messages.sendText?batch=1",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=15
        )
        print(f"[PORTAL] sendText status={resp.status_code} resp={resp.text[:150]}")
        if resp.status_code in (401, 403):
            print("[PORTAL] Sessao expirada, relogando...")
            _portal_logged_in = False
            if portal_login():
                resp = _portal_session.post(
                    f"{PORTAL}/api/trpc/messages.sendText?batch=1",
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=15
                )
                print(f"[PORTAL] retry status={resp.status_code} resp={resp.text[:150]}")
    except Exception as e:
        print(f"[ERRO] enviar: {e}")


@app.route('/', methods=['GET'])
def health():
    return "OK", 200

@app.after_request
def cors(r):
    r.headers['Access-Control-Allow-Origin']  = '*'
    r.headers['Access-Control-Allow-Methods'] = 'GET,POST,OPTIONS'
    r.headers['Access-Control-Allow-Headers'] = 'Content-Type,Authorization'
    return r

@app.route('/webhook', methods=['GET','POST','OPTIONS'])
def webhook():
    if request.method in ('GET','OPTIONS'):
        return jsonify({"ok": True}), 200
    raw = request.get_data(as_text=True)
    print(f"[WEBHOOK] {raw[:400]}")
    data = request.get_json(force=True, silent=True) or {}
    msg = data.get('message', {})
    if msg:
        text    = (msg.get('text') or msg.get('body') or '').strip()
        sender  = msg.get('from') or msg.get('chat_id') or ''
        chat_id = msg.get('chat_id') or msg.get('from') or ''
    else:
        msgs = data.get('messages', [])
        if not msgs:
            return jsonify({"ok": True}), 200
        m       = msgs[0]
        text    = (m.get('body') or m.get('text') or '').strip()
        sender  = m.get('from', '')
        chat_id = sender
    print(f"[MSG] from={sender} | chat={chat_id} | text={text!r}")
    if GROUP not in sender and GROUP not in chat_id:
        print(f"[SKIP] nao e o grupo ({GROUP})")
        return jsonify({"ok": True}), 200
    key = GROUP
    cmd = text.lower().strip('/')
    if cmd == 'buscardocs':
        sessions[key] = {'stage': 'cliente'}
        enviar(GROUP, "Qual cliente voce procura? (nome ou CNPJ)")
    elif key in sessions:
        s = sessions[key]
        if s['stage'] == 'cliente':
            s['cliente'] = text
            s['stage']   = 'ano'
            enviar(GROUP, f"Cliente: {text}. Qual ano? (ex: 2024 ou 2025)")
        elif s['stage'] == 'ano':
            s['ano']   = text
            s['stage'] = 'mes'
            enviar(GROUP, f"Ano: {text}. Qual mes? (ex: 03 ou Marco)")
        elif s['stage'] == 'mes':
            s['mes']   = text
            s['stage'] = 'tipo'
            enviar(GROUP, f"Mes: {text}. Qual tipo? 1.Obrigacoes 2.Apuracao 3.XML 4.DESTDA 5.Sped 6.Outro")
        elif s['stage'] == 'tipo':
            s['tipo'] = text
            enviar(GROUP, "Buscando documentos, aguarde...")
            t = threading.Thread(target=buscar_drive, args=(GROUP, dict(s)), daemon=True)
            t.start()
            del sessions[key]
    return jsonify({"ok": True}), 200


def buscar_drive(para, s):
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        svc_json = os.getenv('SERVICE_ACCOUNT_JSON', '')
        if not svc_json:
            enviar(para, "Credenciais do Drive nao configuradas.")
            return
        creds = service_account.Credentials.from_service_account_info(
            json.loads(svc_json),
            scopes=['https://www.googleapis.com/auth/drive.readonly']
        )
        svc = build('drive', 'v3', credentials=creds)
        cliente = s.get('cliente', '').strip()
        ano     = s.get('ano', '').strip()
        mes     = s.get('mes', '').strip()
        tipo    = s.get('tipo', '').strip()
        print(f"[DRIVE] buscando: {cliente} / {ano} / {mes} / {tipo}")
        def listar_filhos(folder_id, filtro=''):
            q = f"'{folder_id}' in parents and trashed=false"
            if filtro:
                q += f" and name contains '{filtro}'"
            r = svc.files().list(q=q, fields='files(id,name,mimeType,webViewLink)', pageSize=100).execute()
            return r.get('files', [])
        def achar_pasta(folder_id, nome_parcial):
            filhos = listar_filhos(folder_id)
            for f in filhos:
                if f['mimeType'] == 'application/vnd.google-apps.folder':
                    if nome_parcial.upper() in f['name'].upper():
                        return f['id'], f['name']
            return None, None
        cid, cnome = achar_pasta(FOLDER, cliente)
        if not cid:
            todos = listar_filhos(FOLDER)
            for f in todos:
                if f['mimeType'] == 'application/vnd.google-apps.folder':
                    if any(p.upper() in f['name'].upper() for p in cliente.split()):
                        cid, cnome = f['id'], f['name']
                        break
        if not cid:
            enviar(para, f"Cliente nao encontrado: {cliente}")
            return
        aid, anome = achar_pasta(cid, ano)
        if not aid:
            enviar(para, f"Ano {ano} nao encontrado na pasta de {cnome}.")
            return
        mid, mnome = achar_pasta(aid, mes)
        if not mid:
            existentes = [f['name'] for f in listar_filhos(aid) if f['mimeType'] == 'application/vnd.google-apps.folder']
            enviar(para, f"Mes {mes} nao encontrado. Disponiveis: {', '.join(existentes) or 'nenhum'}")
            return
        tid, tnome = achar_pasta(mid, tipo)
        pasta_final = tid if tid else mid
        nome_final  = tnome if tnome else mnome
        arquivos = [f for f in listar_filhos(pasta_final) if f['mimeType'] != 'application/vnd.google-apps.folder']
        if not arquivos:
            enviar(para, f"Nenhum arquivo em {cnome} / {anome} / {nome_final}.")
            return
        msg = f"{cnome} | {anome} / {nome_final} - {len(arquivos)} arquivo(s):\n\n"
        for f in arquivos[:8]:
            link = f.get('webViewLink', f"https://drive.google.com/file/d/{f['id']}/view")
            msg += f"{f['name']}\n{link}\n\n"
        if len(arquivos) > 8:
            msg += f"...e mais {len(arquivos)-8} arquivo(s)."
        enviar(para, msg)
    except Exception as e:
        print(f"[ERRO] buscar_drive: {e}")
        import traceback; traceback.print_exc()
        enviar(para, f"Erro na busca: {e}")


if __name__ == '__main__':
    port = int(os.getenv('PORT', 8080))
    print(f"[START] porta {port}")
    app.run(host='0.0.0.0', port=port, debug=False)

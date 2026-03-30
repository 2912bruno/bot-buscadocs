import os, json, threading, time
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)
sessions = {}

# ── Credenciais W-API diretas ─────────────────────────────────────────────────
WAPI_INSTANCE = os.getenv('WAPI_INSTANCE', 'MMKNGN-NHHKTJ-LDPAZG')
WAPI_TOKEN    = os.getenv('WAPI_TOKEN',    'iscAKCdpJ8NCrDSnWXk4qwDQvCDa6iW8R')
WAPI_BASE     = os.getenv('WAPI_BASE',     'https://api.w-api.app/v1')

GROUP  = os.getenv('GROUP_ID',   '120363039812918773@g.us')
FOLDER = os.getenv('FOLDER_ID',  '1uxLpoZ_oGYAymVBJzA60SH_ZVipFs3y5')

print(f"[OK] INSTANCE={WAPI_INSTANCE} | TOKEN={'sim' if WAPI_TOKEN else 'NAO'}")


def enviar(para, texto):
    """Envia mensagem diretamente via W-API REST."""
    try:
        url = f"{WAPI_BASE}/message/send-text?instanceId={WAPI_INSTANCE}"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {WAPI_TOKEN}"
        }
        payload = {
            "phone": para,
            "message": texto
        }
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
        print(f"[WAPI] status={resp.status_code} resp={resp.text[:200]}")
        return resp.status_code in (200, 201)
    except Exception as e:
        print(f"[ERRO] enviar: {e}")
        return False


# ── Health check ──────────────────────────────────────────────────────────────
@app.route('/', methods=['GET'])
def health():
    return "OK", 200


# ── CORS ──────────────────────────────────────────────────────────────────────
@app.after_request
def cors(r):
    r.headers['Access-Control-Allow-Origin']  = '*'
    r.headers['Access-Control-Allow-Methods'] = 'GET,POST,OPTIONS'
    r.headers['Access-Control-Allow-Headers'] = 'Content-Type,Authorization'
    return r


# ── Webhook principal ─────────────────────────────────────────────────────────
@app.route('/webhook', methods=['GET','POST','OPTIONS'])
def webhook():
    if request.method in ('GET','OPTIONS'):
        return jsonify({"ok": True}), 200

    raw = request.get_data(as_text=True)
    print(f"[WEBHOOK] {raw[:400]}")

    data = request.get_json(force=True, silent=True) or {}

    # ── Extrai texto e remetente ──────────────────────────────────────────────
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

    # ── Só responde ao grupo correto ──────────────────────────────────────────
    if GROUP not in sender and GROUP not in chat_id:
        print(f"[SKIP] não é o grupo ({GROUP})")
        return jsonify({"ok": True}), 200

    key = GROUP

    # ── Fluxo de conversa ─────────────────────────────────────────────────────
    cmd = text.lower().strip('/')
    if cmd == 'buscardocs':
        sessions[key] = {'stage': 'cliente'}
        enviar(GROUL, "🔍 Qual *cliente* você procura?\n(nome ou CNPJ)")

    elif key in sessions:
        s = sessions[key]

        if s['stage'] == 'cliente':
            s['cliente'] = text
            s['stage']   = 'ano'
            enviar(GROUL, f"✅ Cliente: *{text}*\n\nQual *ano*? (ex: 2024 ou 2025)")

        elif s['stage'] == 'ano':
            s['ano']   = text
            s['stage'] = 'mes'
            enviar(GROUL, f"✅ Ano: *{text}*\n\nQual *mês*? (ex: 03 ou Marco)")

        elif s['stage'] == 'mes':
            s['mes']   = text
            s['stage'] = 'tipo'
            enviar(GROUL, f"✅ Mês: *{text}*\n\nQual *tipo* de documento?\n1. Obrigacoes\n2. Apuracao\n3. XML\n4. DESTDA\n5. Sped\n6. Outro")

        elif s['stage'] == 'tipo':
            s['tipo'] = text
            enviar(GROUL, "🔎 Buscando documentos, aguarde...")
            t = threading.Thread(target=buscar_drive, args=(GROUL, dict(s)), daemon=True)
            t.start()
            del sessions[key]

    return jsonify({"ok": True}), 200


# ── Busca no Google Drive ─────────────────────────────────────────────────────
def buscar_drive(para, s):
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        svc_json = os.getenv('SERVICE_ACCOUNT_JSON', '')
        if not svc_json:
            enviar(para, "❌ Credenciais do Drive não configuradas.")
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
            enviar(para, f"❌ Cliente não encontrado: *{cliente}*\nVerifique o nome na pasta do Drive.")
            return

        aid, anome = achar_pasta(cid, ano)
        if not aid:
            enviar(para, f"❌ Ano *{ano}* não encontrado na pasta de *{cnome}*.")
            return

        mid, mnome = achar_pasta(aid, mes)
        if not mid:
            existentes = [f['name'] for f in listar_filhos(aid) if f['mimeType'] == 'application/vnd.google-apps.folder']
            enviar(para, f"❌ Mês *{mes}* não encontrado.\nMeses disponíveis: {', '.join(existentes) or 'nenhum'}")
            return

        tid, tnome = achar_pasta(mid, tipo)
        pasta_final = tid if tid else mid
        nome_final  = tnome if tnome else mnome

        arquivos = [f for f in listar_filhos(pasta_final) if f['mimeType'] != 'application/vnd.google-apps.folder']

        if not arquivos:
            enviar(para, f"❌ Nenhum arquivo em *{cnome} / {anome} / {nome_final}*.")
            return

        msg = f"📂 *{cnome}* | {anome} / {nome_final}\n{len(arquivos)} arquivo(s):\n\n"
        for f in arquivos[:8]:
            link = f.get('webViewLink', f"https://drive.google.com/file/d/{f['id']}/view")
            msg += f"📄 {f['name']}\n{link}\n\n"
        if len(arquivos) > 8:
            msg += f"_...e mais {len(arquivos)-8} arquivo(s)._"

        enviar(para, msg)

    except Exception as e:
        print(f"[ERRO] buscar_drive: {e}")
        import traceback; traceback.print_exc()
        enviar(para, f"❌ Erro na busca: {e}")


# ── Inicialização ─────────────────────────────────────────────────────────────
if __name__ == '__main__':
    port = int(os.getenv('PORT', 8080))
    print(f"[START] porta {port}")
    app.run(host='0.0.0.0', port=port, debug=False)

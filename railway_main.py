import os
import json
import threading
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)
user_sessions = {}

WAPI_TOKEN = os.getenv('WAPI_TOKEN', '')
WAPI_NUMBER = os.getenv('WAPI_NUMBER', '')
GROUP_ID = os.getenv('GROUP_ID', '120363039812918773@g.us')
FOLDER_ID = os.getenv('FOLDER_ID', '1uxLpoZ_oGYAymVBJzA60SH_ZVipFs3y5')

print("[STARTUP] WAPI_TOKEN: " + ("OK" if WAPI_TOKEN else "FALTANDO"))
print("[STARTUP] WAPI_NUMBER: " + ("OK" if WAPI_NUMBER else "FALTANDO"))


@app.route('/', methods=['GET'])
def health():
    return "OK", 200


@app.after_request
def add_cors(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    return response


@app.route('/webhook', methods=['GET', 'POST', 'OPTIONS'])
def webhook():
    if request.method in ['GET', 'OPTIONS']:
        return jsonify({"status": "ok"}), 200
    try:
        data = request.get_json(force=True, silent=True) or {}
        print("[WEBHOOK] payload: " + json.dumps(data)[:300])

        msg = data.get('message', {})
        if msg:
            text = (msg.get('text') or msg.get('body') or '').strip()
            from_id = msg.get('from', '') or msg.get('chat_id', '')
            chat_id = msg.get('chat_id', '') or msg.get('from', '')
        else:
            messages = data.get('messages', [])
            if not messages:
                return jsonify({"status": "ok"}), 200
            msg = messages[0]
            text = (msg.get('body') or msg.get('text') or '').strip()
            from_id = msg.get('from', '')
            chat_id = from_id

        print("[MSG] from=" + str(from_id) + " chat=" + str(chat_id) + " text=" + str(text[:80]))

        if GROUP_ID not in from_id and GROUP_ID not in chat_id:
            print("[SKIP] nao e o grupo: from=" + from_id + " chat=" + chat_id)
            return jsonify({"status": "ok"}), 200

        session_key = GROUP_ID

        if text.lower() in ['/buscardocs', 'buscardocs']:
            user_sessions[session_key] = {'stage': 'cliente'}
            send_msg(GROUP_ID, "Qual cliente voce procura? (nome ou CNPJ)")
        elif session_key in user_sessions:
            s = user_sessions[session_key]
            stage = s.get('stage')
            if stage == 'cliente':
                s['cliente'] = text
                s['stage'] = 'ano'
                send_msg(GROUP_ID, "Cliente: " + text + "\nQual ano? (ex: 2024 ou 2025)")
            elif stage == 'ano':
                s['ano'] = text
                s['stage'] = 'mes'
                send_msg(GROUP_ID, "Ano: " + text + "\nQual mes? (ex: 03 ou Marco)")
            elif stage == 'mes':
                s['mes'] = text
                s['stage'] = 'tipo'
                send_msg(GROUP_ID, "Mes: " + text + "\nQual tipo de documento?\n1. Obrigacoes\n2. Apuracao\n3. XML\n4. DESTDA\n5. Sped\n6. Outro")
            elif stage == 'tipo':
                s['tipo'] = text
                send_msg(GROUP_ID, "Buscando documentos, aguarde...")
                t = threading.Thread(target=buscar_e_enviar, args=(GROUP_ID, dict(s)))
                t.daemon = True
                t.start()
                del user_sessions[session_key]
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        print("[ERROR] webhook: " + str(e))
        return jsonify({"status": "ok"}), 200


def send_msg(to, body):
    try:
        if not WAPI_TOKEN:
            print("[ERROR] WAPI_TOKEN nao configurado")
            return False
        url = "https://api.w-api.app/v1/message/send-text"
        payload = {"to": to, "body": body}
        headers = {
            "Authorization": "Bearer " + WAPI_TOKEN,
            "Content-Type": "application/json",
            "instanceid": WAPI_TOKEN
        }
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
        print("[WAPI] status=" + str(resp.status_code) + " resp=" + resp.text[:150])
        return resp.status_code in [200, 201]
    except Exception as e:
        print("[ERROR] send_msg: " + str(e))
        return False


def buscar_e_enviar(to, session):
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        cliente = session.get('cliente', '')
        ano = session.get('ano', '')
        mes = session.get('mes', '')
        tipo = session.get('tipo', '')
        print("[BUSCA] " + cliente + " / " + ano + " / " + mes + " / " + tipo)

        svc_json = os.getenv('SERVICE_ACCOUNT_JSON')
        if not svc_json:
            send_msg(to, "Erro: SERVICE_ACCOUNT_JSON nao configurado.")
            return

        creds = service_account.Credentials.from_service_account_info(
            json.loads(svc_json),
            scopes=['https://www.googleapis.com/auth/drive.readonly']
        )
        svc = build('drive', 'v3', credentials=creds)

        r = svc.files().list(
            q="'" + FOLDER_ID + "' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
            fields='files(id, name)', pageSize=100
        ).execute()
        clientes = [f for f in r.get('files', []) if cliente.upper() in f['name'].upper()]
        if not clientes:
            send_msg(to, "Cliente nao encontrado: " + cliente)
            return
        cliente_id = clientes[0]['id']
        cliente_nome = clientes[0]['name']

        r = svc.files().list(
            q="'" + cliente_id + "' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
            fields='files(id, name)', pageSize=20
        ).execute()
        anos = [f for f in r.get('files', []) if ano in f['name']]
        if not anos:
            send_msg(to, "Ano nao encontrado: " + ano + " para " + cliente_nome)
            return
        ano_id = anos[0]['id']

        r = svc.files().list(
            q="'" + ano_id + "' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
            fields='files(id, name)', pageSize=20
        ).execute()
        meses = [f for f in r.get('files', []) if mes.upper() in f['name'].upper()]
        if not meses:
            opcoes = ", ".join([f['name'] for f in r.get('files', [])])
            send_msg(to, "Mes nao encontrado: " + mes + "\nOpcoes: " + opcoes)
            return
        mes_id = meses[0]['id']

        r = svc.files().list(
            q="'" + mes_id + "' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
            fields='files(id, name)', pageSize=20
        ).execute()
        tipos_lista = r.get('files', [])
        tipos_match = [f for f in tipos_lista if tipo.upper() in f['name'].upper()]
        if not tipos_match:
            opcoes = ", ".join([f['name'] for f in tipos_lista])
            send_msg(to, "Tipo nao encontrado: " + tipo + "\nOpcoes:\n" + opcoes)
            return
        tipo_id = tipos_match[0]['id']
        tipo_nome = tipos_match[0]['name']

        r = svc.files().list(
            q="'" + tipo_id + "' in parents and trashed=false",
            fields='files(id, name, webViewLink)', pageSize=50
        ).execute()
        pdfs = r.get('files', [])

        if pdfs:
            msg = "Documentos em '" + tipo_nome + "':\n\n"
            for i, pdf in enumerate(pdfs[:10], 1):
                link = pdf.get('webViewLink') or "https://drive.google.com/file/d/" + pdf['id'] + "/view"
                msg += str(i) + ". " + pdf['name'] + "\n" + link + "\n\n"
            send_msg(to, msg)
        else:
            send_msg(to, "Nenhum documento encontrado em " + tipo_nome)

    except Exception as e:
        print("[ERROR] buscar_e_enviar: " + str(e))
        send_msg(to, "Erro ao buscar: " + str(e)[:150])


if __name__ == '__main__':
    port = int(os.getenv('PORT', 8080))
    print("[STARTUP] Rodando na porta " + str(port))
    app.run(host='0.0.0.0', port=port, debug=False)

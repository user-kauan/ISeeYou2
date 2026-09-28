"""
Como funciona:
  1. O celular abre uma pagina servida por este programa (pelo navegador, sem instalar app).
  2. A pagina manda os quadros da camera do celular para o PC.
  3. O PC roda o YOLO e as regras do monitor.py e devolve a imagem com as caixas, mais o estado
     de cada regra (cabecalho X-Estados), que a pagina mostra na faixa, na lista e nas ocorrencias.

Precisa estar na MESMA PASTA do monitor.py (ele reaproveita as regras e os limiares de la)
e do pagina.html (o visual da pagina que abre no celular).
Uso (com o ambiente virtual ativado):   python servidor_remoto.py

IMPORTANTE: o navegador do celular so libera a camera em paginas HTTPS. Use o certificado gerado
pelo "tailscale cert" e acesse pela VPN (Tailscale). Nao exponha esta porta
na internet aberta.
"""
import secrets
import ssl
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import cv2
import numpy as np

import monitor as m

# --- Ajustes (edite aqui) ---------------------------------------------------
PORTA = 8443
CHAVE = ""  # vazio = gera uma chave aleatoria a cada execucao (aparece no terminal)
CERT = r"C:\projetos\epi\certs\pc.crt"  # gerado por "tailscale cert"
KEY = r"C:\projetos\epi\certs\pc.key"
QUALIDADE_JPEG = 70  # qualidade da imagem devolvida ao celular (menor = mais leve)
# ----------------------------------------------------------------------------

# O visual da pagina fica em pagina.html (na mesma pasta deste arquivo). Pode editar e dar F5 no celular.
PAGINA_ARQ = Path(__file__).with_name("pagina.html")


class Processador:
    """Roda o modelo e as regras do monitor.py em cada quadro recebido."""

    def __init__(self, model, model_pose=None):
        self.model = model
        self.model_pose = model_pose  # opcional: desenha o esqueleto (ve m.MODELO_POSE)
        self.regras = {n: m.Regra(n) for n, ligada in m.REGRAS_ATIVAS.items() if ligada}
        self.conf_min = min(m.CONF.values())
        self.lock = threading.Lock()  # a placa de video atende um quadro por vez

    def processar(self, frame):
        with self.lock:
            r = self.model(frame, device=0, imgsz=640, conf=self.conf_min,
                           classes=list(m.NOMES), verbose=False)[0]
            deteccoes = []
            for c, cf, caixa in zip(r.boxes.cls.tolist(), r.boxes.conf.tolist(), r.boxes.xyxy.tolist()):
                c = int(c)
                if cf >= m.CONF[c]:
                    deteccoes.append((c, cf, tuple(caixa)))

            pessoas = []
            if self.model_pose is not None:
                r_pose = self.model_pose(frame, device=0, imgsz=640, verbose=False)[0]
                pessoas = m.pessoas_da_pose(r_pose)

            status = m.avaliar(deteccoes, frame.shape[0])
            estados = {}
            for nome, regra in self.regras.items():
                antes = regra.ativa
                regra.atualizar(status[nome] == "violacao")
                estados[nome] = m.estado_visual(regra.ativa, status[nome])
                if regra.ativa != antes:
                    m.ao_mudar_alerta(nome, regra.ativa)
            saida = m.desenhar(frame, deteccoes, estados, pessoas=pessoas, faixa=False)  # a pagina mostra os estados
        ok, buf = cv2.imencode(".jpg", saida, [cv2.IMWRITE_JPEG_QUALITY, QUALIDADE_JPEG])
        return buf.tobytes(), estados


class Servidor(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, endereco, handler, chave, processador, contexto_ssl=None):
        super().__init__(endereco, handler)
        self.chave = chave
        self.processador = processador
        self.contexto_ssl = contexto_ssl

    def get_request(self):
        sock, addr = super().get_request()
        if self.contexto_ssl is not None:
            # o "aperto de mao" do TLS acontece na thread da conexao, e nao aqui:
            # assim um cliente lento ou travado nao bloqueia os outros
            sock = self.contexto_ssl.wrap_socket(sock, server_side=True, do_handshake_on_connect=False)
        return sock, addr

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], OSError):  # queda de conexao, TLS recusado, timeout: barulho normal
            return
        super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"    # mantem a conexao aberta entre os quadros (o padrao HTTP/1.0 abria uma nova por quadro)
    disable_nagle_algorithm = True   # envia cabecalho e corpo sem esperar (menos latencia)
    timeout = 60                     # fecha conexoes ociosas

    def log_message(self, fmt, *args):  # silencia o log padrao (um por quadro)
        pass

    def _chave_ok(self):
        recebida = parse_qs(urlparse(self.path).query).get("k", [""])[0]
        return secrets.compare_digest(recebida.encode(), self.server.chave.encode())

    def _responder(self, codigo, corpo=b"", tipo="text/plain; charset=utf-8", fechar=False, extras=None):
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        for nome, valor in (extras or {}).items():
            self.send_header(nome, valor)
        if fechar:  # usado quando o corpo da requisicao nao foi lido (a conexao ficaria fora de sincronia)
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        if not self._chave_ok():
            return self._responder(403, b"acesso negado")
        rota = urlparse(self.path).path
        if rota == "/":
            try:
                corpo = PAGINA_ARQ.read_bytes()
            except OSError:
                return self._responder(500, f"Nao encontrei {PAGINA_ARQ}".encode())
            return self._responder(200, corpo, "text/html; charset=utf-8")
        if rota == "/saude":
            return self._responder(200, b"ok")
        self._responder(404, b"nao encontrado")

    def do_POST(self):
        if not self._chave_ok():
            return self._responder(403, b"acesso negado", fechar=True)
        if urlparse(self.path).path != "/frame":
            return self._responder(404, b"nao encontrado", fechar=True)
        try:
            n = int(self.headers.get("Content-Length", 0))
        except ValueError:
            n = 0
        if n <= 0 or n > 3_000_000:
            return self._responder(413, b"tamanho invalido", fechar=True)
        dados = self.rfile.read(n)
        frame = cv2.imdecode(np.frombuffer(dados, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            return self._responder(400, b"imagem invalida")
        jpg, estados = self.server.processador.processar(frame)
        cabecalho = ",".join(f"{nome}={estado}" for nome, estado in estados.items())
        self._responder(200, jpg, "image/jpeg", extras={"X-Estados": cabecalho})


def criar_servidor(processador, porta, chave):
    """Cria o servidor; usa HTTPS se os arquivos de certificado existirem. Retorna (servidor, https)."""
    ctx = None
    if Path(CERT).exists() and Path(KEY).exists():
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(CERT, KEY)
    srv = Servidor(("0.0.0.0", porta), Handler, chave, processador, ctx)
    return srv, ctx is not None


def main():
    from ultralytics import YOLO  # importado aqui para os testes poderem trocar o modelo

    if not PAGINA_ARQ.exists():
        raise SystemExit(f"Nao encontrei {PAGINA_ARQ}. Deixe o pagina.html na mesma pasta do servidor_remoto.py.")
    model = YOLO(m.MODELO)
    model(np.zeros((640, 640, 3), np.uint8), device=0, verbose=False)  # aquecimento da placa
    model_pose = None
    if m.MODELO_POSE:
        model_pose = YOLO(m.MODELO_POSE)
        model_pose(np.zeros((640, 640, 3), np.uint8), device=0, verbose=False)
    chave = CHAVE or secrets.token_urlsafe(8)
    srv, https = criar_servidor(Processador(model, model_pose), PORTA, chave)

    esquema = "https" if https else "http"
    print(f"Servidor no ar na porta {PORTA} ({esquema.upper()}).")
    if not https:
        print("ATENCAO: sem certificado (HTTP). A camera do celular so abre em HTTPS.")
        print(f"         Esperado em: {CERT} e {KEY}")
    print(f"No celular, abra:  {esquema}://NOME-DO-PC.SEU-TAILNET.ts.net:{PORTA}/?k={chave}")
    print("Para parar: Ctrl+C")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nEncerrado.")


if __name__ == "__main__":
    main()

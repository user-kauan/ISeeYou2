"""
Monitor de EPI em tempo real: detecta, aplica regras de violacao e suaviza no tempo.
Uso (com o ambiente virtual ativado):   python monitor.py

Regras (baseadas no comportamento dos labels do SH17):
  LUVAS    : caixa "hands" (mao nua) sem "gloves" por cima
  CAPACETE : caixa "head" sem "helmet" por cima
  OCULOS   : caixa "face" sem "glasses" por cima
  COLETE   : caixa "person" sem "safety-vest" por cima
O alerta so liga se a violacao aparecer na maior parte dos ultimos quadros (evita o "pipocar").
Limitacao: as regras valem para o quadro como um todo; nao acompanham cada pessoa individualmente.
"""
import time
from collections import deque

import cv2

# --- Ajustes (edite aqui) ---------------------------------------------------
MODELO = r"C:\projetos\epi\runs\treino1\weights\best.pt"
FONTE = 0  # 0 = camera; ou caminho de um video, ex.: r"C:\projetos\epi\teste.mp4"

# Deixe "" se quiser desligar o esqueleto e usar so os modelos originais.
MODELO_POSE = "yolo11n-pose.pt"

# Quais regras ficam ligadas (True) ou desligadas (False)
REGRAS_ATIVAS = {"CAPACETE": True, "LUVAS": True, "OCULOS": True, "COLETE": True}

JANELA = 15     # quantos quadros o alerta "olha para tras" (~0,5 s a 30 quadros/s)
LIGA = 0.70     # o alerta liga quando a violacao aparece em pelo menos 70% da janela
DESLIGA = 0.30  # o alerta desliga quando cai para 30% ou menos
# ----------------------------------------------------------------------------

NOMES = {0: "person", 3: "face", 8: "glasses", 9: "gloves", 10: "helmet",
         11: "hands", 12: "head", 16: "safety-vest"}
PESSOA, ROSTO, OCULOS, LUVA, CAPACETE, MAO, CABECA, COLETE = 0, 3, 8, 9, 10, 11, 12, 16

# Confianca minima por classe. Os EPIs (que o modelo perde com facilidade) ficam mais baixos:
# assim um EPI real e menos vezes "esquecido", o que gera menos falso alarme.
CONF = {PESSOA: 0.40, ROSTO: 0.40, CABECA: 0.40, MAO: 0.40,
        OCULOS: 0.20, LUVA: 0.25, CAPACETE: 0.25, COLETE: 0.25}

# Altura minima (fracao da altura do quadro) para julgar uma caixa; ignora as muito pequenas/distantes.
ALTURA_MIN = {PESSOA: 0.25, CABECA: 0.05, ROSTO: 0.04, MAO: 0.05}

# regra -> (classe que precisa do EPI, classe do EPI)
REGRAS = {
    "CAPACETE": (CABECA, CAPACETE),
    "LUVAS": (MAO, LUVA),  # "hands" = mao nua; mao enluvada aparece como "gloves"
    "OCULOS": (ROSTO, OCULOS),
    "COLETE": (PESSOA, COLETE),
}

# So estas caixas sao desenhadas no video: os 4 EPIs de verdade. As demais (person, face, head,
# hands) so servem para a logica das regras acima; no desenho, o esqueleto faz esse papel.
CLASSES_EPI = {OCULOS, LUVA, CAPACETE, COLETE}

# --- Esqueleto: os 17 pontos do corpo, na ordem que o modelo de pose devolve ---
NARIZ, OLHO_E, OLHO_D, ORELHA_E, ORELHA_D = 0, 1, 2, 3, 4
OMBRO_E, OMBRO_D, COTOVELO_E, COTOVELO_D = 5, 6, 7, 8
PULSO_E, PULSO_D, QUADRIL_E, QUADRIL_D = 9, 10, 11, 12
JOELHO_E, JOELHO_D, TORNOZELO_E, TORNOZELO_D = 13, 14, 15, 16

# Pares de pontos que formam as linhas do esqueleto.
OSSOS = [
    (NARIZ, OLHO_E), (NARIZ, OLHO_D), (OLHO_E, ORELHA_E), (OLHO_D, ORELHA_D),
    (OMBRO_E, OMBRO_D),
    (OMBRO_E, COTOVELO_E), (COTOVELO_E, PULSO_E),
    (OMBRO_D, COTOVELO_D), (COTOVELO_D, PULSO_D),
    (OMBRO_E, QUADRIL_E), (OMBRO_D, QUADRIL_D), (QUADRIL_E, QUADRIL_D),
    (QUADRIL_E, JOELHO_E), (JOELHO_E, TORNOZELO_E),
    (QUADRIL_D, JOELHO_D), (JOELHO_D, TORNOZELO_D),
]

CONF_PONTO_MIN = 0.5  # abaixo disso, o modelo nao tem certeza de onde o ponto esta, e ele e ignorado


def sobreposicao(a, b):
    """Area da intersecao dividida pela area da MENOR caixa. Caixas: (x1, y1, x2, y2)."""
    iw = min(a[2], b[2]) - max(a[0], b[0])
    ih = min(a[3], b[3]) - max(a[1], b[1])
    if iw <= 0 or ih <= 0:
        return 0.0
    menor = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return iw * ih / menor if menor > 0 else 0.0


def pequena_demais(classe, caixa, altura_quadro):
    """True se a caixa e pequena demais (longe demais) para a regra poder julgar."""
    return (caixa[3] - caixa[1]) / altura_quadro < ALTURA_MIN.get(classe, 0)


def avaliar(deteccoes, altura_quadro, limiar=0.5):
    """deteccoes: lista de (classe, confianca, (x1, y1, x2, y2)).
    Para cada regra, diz o que ESTE quadro mostra:
      "violacao"  : existe uma caixa base (ex.: mao nua) sem o EPI por cima
      "ok"        : ha EPI a vista e nenhuma violacao
      "pequeno"   : ha caixa base, mas pequena demais para julgar (longe da camera)
      "sem-alvo"  : nada a avaliar (nenhuma mao/cabeca/rosto/pessoa na imagem)"""
    por_classe = {}
    for c, _, caixa in deteccoes:
        por_classe.setdefault(c, []).append(caixa)
    resultado = {}
    for nome, (base, epi) in REGRAS.items():
        bases = por_classe.get(base, [])
        epis = por_classe.get(epi, [])
        julgaveis = [b for b in bases if not pequena_demais(base, b, altura_quadro)]
        if any(not any(sobreposicao(b, e) >= limiar for e in epis) for b in julgaveis):
            resultado[nome] = "violacao"
        elif epis:
            resultado[nome] = "ok"
        elif bases:
            resultado[nome] = "pequeno"
        else:
            resultado[nome] = "sem-alvo"
    return resultado


def violacoes(deteccoes, altura_quadro, limiar=0.5):
    """Versao simples de avaliar(): {regra: True/False}, True se este quadro tem violacao."""
    return {nome: status == "violacao" for nome, status in avaliar(deteccoes, altura_quadro, limiar).items()}


def estado_visual(alerta_ativo, status):
    """O que mostrar para o usuario: "alerta" (ligado), "verificando" (viu a violacao, mas ainda
    nao confirmou ao longo dos quadros) ou o proprio status do quadro (ok / pequeno / sem-alvo)."""
    
    if alerta_ativo:
        return "alerta"
    if status == "violacao":
        return "verificando"
    return status


class Regra:
    """Suaviza no tempo: o alerta liga/desliga so quando a tendencia dos ultimos quadros muda."""

    def __init__(self, nome):
        self.nome = nome
        self.historico = deque(maxlen=JANELA)
        self.ativa = False

    def atualizar(self, violacao):
        self.historico.append(violacao)
        if len(self.historico) < self.historico.maxlen:
            return self.ativa  # ainda aquecendo a janela
        taxa = sum(self.historico) / len(self.historico)
        if self.ativa and taxa <= DESLIGA:
            self.ativa = False
        elif not self.ativa and taxa >= LIGA:
            self.ativa = True
        return self.ativa


def pessoas_da_pose(resultado_pose):
    """Le o resultado do modelo de pose e devolve uma lista com os pontos de cada pessoa:
    [{"pontos": [(x, y), ...] (17 pontos), "conf": [c, ...] (17 confiancas)}, ...]."""
    pessoas = []
    pontos_kp = getattr(resultado_pose, "keypoints", None)
    if pontos_kp is None or pontos_kp.xy is None:
        return pessoas
    todos_xy = pontos_kp.xy.tolist()
    todos_conf = pontos_kp.conf.tolist() if pontos_kp.conf is not None else None
    for i, pontos in enumerate(todos_xy):
        conf = todos_conf[i] if todos_conf is not None else [1.0] * len(pontos)
        pessoas.append({"pontos": [tuple(p) for p in pontos], "conf": conf})
    return pessoas


def _media_pontos(pontos, conf, indices, limiar):
    """Media dos pontos (dentre os indices dados) com confianca suficiente; None se nenhum."""
    validos = [pontos[i] for i in indices if conf[i] >= limiar]
    if not validos:
        return None
    return (sum(p[0] for p in validos) / len(validos), sum(p[1] for p in validos) / len(validos))


def pontos_da_regra(nome, pessoa, limiar=CONF_PONTO_MIN):
    """Em que ponto do corpo desenhar o indicador desta regra. Devolve uma lista de pontos
    (0, 1 ou 2 -- LUVAS pode marcar os dois pulsos)."""
    pontos, conf = pessoa["pontos"], pessoa["conf"]
    if nome in ("CAPACETE", "OCULOS"):
        centro = _media_pontos(pontos, conf, (NARIZ, OLHO_E, OLHO_D), limiar)
        if not centro:
            return []
        if nome == "OCULOS":
            return [centro]  # na altura dos olhos
        # CAPACETE: sobe para a regiao do topo da cabeca, senao ficaria em cima do ponto dos oculos.
        # A distancia entre os olhos (ou orelhas) serve de referencia do tamanho da cabeca nesta
        # distancia da camera; sem isso, sobe um valor fixo, so para nao empilhar os dois pontos.
        if conf[OLHO_E] >= limiar and conf[OLHO_D] >= limiar:
            referencia = abs(pontos[OLHO_E][0] - pontos[OLHO_D][0])
        elif conf[ORELHA_E] >= limiar and conf[ORELHA_D] >= limiar:
            referencia = abs(pontos[ORELHA_E][0] - pontos[ORELHA_D][0])
        else:
            referencia = 25
        return [(centro[0], centro[1] - referencia)]
    if nome == "LUVAS":
        return [pontos[i] for i in (PULSO_E, PULSO_D) if conf[i] >= limiar]
    if nome == "COLETE":
        p = _media_pontos(pontos, conf, (OMBRO_E, OMBRO_D, QUADRIL_E, QUADRIL_D), limiar)
        return [p] if p else []
    return []


COR_ESQUELETO = (200, 200, 200)  # BGR: cinza claro, neutro


def desenhar_esqueleto(frame, pessoas, limiar=CONF_PONTO_MIN):
    """Desenha o esqueleto de cada pessoa encontrada pelo modelo do pose."""
    for pessoa in pessoas:
        pontos, conf = pessoa["pontos"], pessoa["conf"]
        for a, b in OSSOS:
            if conf[a] >= limiar and conf[b] >= limiar:
                pa = (int(pontos[a][0]), int(pontos[a][1]))
                pb = (int(pontos[b][0]), int(pontos[b][1]))
                cv2.line(frame, pa, pb, COR_ESQUELETO, 2, cv2.LINE_AA)
        for i, p in enumerate(pontos):
            if conf[i] >= limiar:
                cv2.circle(frame, (int(p[0]), int(p[1])), 3, COR_ESQUELETO, -1, cv2.LINE_AA)


def ao_mudar_alerta(nome, ativo):
    
    hora = time.strftime("%H:%M:%S")
    print(f"[{hora}] {nome}: {'ALERTA LIGADO' if ativo else 'alerta desligado'}")


ROTULOS = {"alerta": "ALERTA", "verificando": "verificando", "ok": "ok",
           "pequeno": "longe demais", "sem-alvo": "sem alvo"}
CORES = {"alerta": (0, 0, 255), "verificando": (0, 200, 255), "ok": (0, 200, 0),
         "pequeno": (150, 150, 150), "sem-alvo": (150, 150, 150)}  # BGR


def desenhar(frame, deteccoes, estados, pessoas=None, faixa=True):
    """Desenha o esqueleto (se pessoas for dado), a caixa de cada EPI detectado (so os 4: capacete,
    oculos, luva, colete -- as demais classes so entram na logica, nao no desenho) e um pontinho
    colorido no corpo indicando o estado de cada regra. Se faixa=True, tambem escreve a lista de
    estados no canto (util so na janela local; a pagina do celular ja mostra isso por fora).
    estados: {regra: "alerta" | "verificando" | "ok" | "pequeno" | "sem-alvo"} (True/False tambem vale)."""
    estados = {n: (("alerta" if e else "ok") if isinstance(e, bool) else e) for n, e in estados.items()}
    pessoas = pessoas or []

    desenhar_esqueleto(frame, pessoas)

    for c, conf, (x1, y1, x2, y2) in deteccoes:
        if c not in CLASSES_EPI:
            continue
        cor = (0, 200, 0)
        cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), cor, 2)
        cv2.putText(frame, NOMES[c], (int(x1), max(int(y1) - 6, 14)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, cor, 1, cv2.LINE_AA)

    for pessoa in pessoas:
        for nome, estado in estados.items():
            cor = CORES[estado]
            for (x, y) in pontos_da_regra(nome, pessoa):
                cv2.circle(frame, (int(x), int(y)), 10, cor, -1, cv2.LINE_AA)
                cv2.circle(frame, (int(x), int(y)), 10, (20, 20, 20), 2, cv2.LINE_AA)  # contorno escuro p/ contraste

    if faixa:
        y = 28
        for nome, estado in estados.items():
            cv2.putText(frame, f"{nome}: {ROTULOS[estado]}", (10, y),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, CORES[estado], 2)
            y += 30
    return frame


def main():
    from ultralytics import YOLO  # importado aqui para as funcoes acima poderem ser testadas sem o YOLO

    model = YOLO(MODELO)
    model_pose = YOLO(MODELO_POSE) if MODELO_POSE else None
    if isinstance(FONTE, int):
        cap = cv2.VideoCapture(FONTE, cv2.CAP_DSHOW)  # camera no Windows
    else:
        cap = cv2.VideoCapture(FONTE)  # arquivo de video
    if not cap.isOpened():
        raise SystemExit("Nao consegui abrir a fonte de video. Confira a camera ou o caminho do arquivo.")

    regras = {n: Regra(n) for n, ligada in REGRAS_ATIVAS.items() if ligada}
    conf_min = min(CONF.values())

    while True:
        ok, frame = cap.read()
        if not ok:  # fim do video ou falha da camera
            break
        r = model(frame, device=0, imgsz=640, conf=conf_min, classes=list(NOMES), verbose=False)[0]

        deteccoes = []
        for c, cf, caixa in zip(r.boxes.cls.tolist(), r.boxes.conf.tolist(), r.boxes.xyxy.tolist()):
            c = int(c)
            if cf >= CONF[c]:
                deteccoes.append((c, cf, tuple(caixa)))

        pessoas = []
        if model_pose is not None:
            r_pose = model_pose(frame, device=0, imgsz=640, verbose=False)[0]
            pessoas = pessoas_da_pose(r_pose)

        status = avaliar(deteccoes, frame.shape[0])
        estados = {}
        for nome, regra in regras.items():
            antes = regra.ativa
            regra.atualizar(status[nome] == "violacao")
            estados[nome] = estado_visual(regra.ativa, status[nome])
            if regra.ativa != antes:
                ao_mudar_alerta(nome, regra.ativa)

        cv2.imshow("EPI - aperte q para sair", desenhar(frame, deteccoes, estados, pessoas=pessoas))
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()

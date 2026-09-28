"""
Captura quadros automaticamente para montar um dataset do SEU ambiente.
Uso (com o ambiente virtual ativado):   python capturar.py

MODO = "intervalo": salva 1 quadro a cada INTERVALO_S segundos (nao precisa do modelo).
MODO = "incerteza": salva quando o modelo esta em duvida sobre as classes de interesse
                    (confianca media, ou "hands" e "gloves" na mesma mao). Alem disso salva
                    1 quadro a cada INTERVALO_BASE_S segundos, porque erros totais (o modelo nao
                    ve nada) nao disparam a duvida.

Cada quadro e salvo LIMPO (sem caixas) em PASTA\\images. Com PRE_ANOTAR = True, o modelo tambem
grava a sugestao de labels (formato YOLO) em PASTA\\labels_pre. Essas sugestoes PRECISAM ser
revisadas: o modelo repete os proprios erros se ninguem corrigir.
"""
import time
from datetime import datetime
from pathlib import Path

import cv2

# --- Ajustes (edite aqui) ---------------------------------------------------
FONTE = 0                 # 0 = camera; ou caminho de um video, ex.: r"C:\projetos\epi\video1.mp4"
MODO = "incerteza"        # "intervalo" ou "incerteza"
INTERVALO_S = 1.0         # intervalo minimo entre quadros salvos (evita quadros quase iguais)
INTERVALO_BASE_S = 5.0    # no modo "incerteza": tambem salva 1 quadro a cada X s (0 = desligado)
MAX_QUADROS = 600         # para sozinho ao chegar nesse numero
PRE_ANOTAR = True         # grava sugestoes de labels feitas pelo modelo
MOSTRAR = True            # mostra a janela com o contador
PASTA = Path(r"C:\projetos\epi\meus_dados")
MODELO = r"C:\projetos\epi\runs\treino1\weights\best.pt"

CLASSES_INTERESSE = {9, 11}   # gloves, hands (ajuste conforme o EPI que voce esta coletando)
FAIXA_DUVIDA = (0.20, 0.60)   # confianca "em duvida"
CONF_MIN = 0.20               # confianca minima para o modelo reportar algo
CONF_PRE = 0.25               # confianca minima para entrar nas sugestoes de labels
# ----------------------------------------------------------------------------

# Mesmos IDs do SH17, para as imagens novas poderem ser misturadas ao dataset original.
NOMES = {0: "person", 3: "face", 8: "glasses", 9: "gloves", 10: "helmet",
         11: "hands", 12: "head", 16: "safety-vest"}
LUVA, MAO = 9, 11


def sobreposicao(a, b):
    """Area da intersecao dividida pela area da MENOR caixa. Caixas: (x1, y1, x2, y2)."""
    iw = min(a[2], b[2]) - max(a[0], b[0])
    ih = min(a[3], b[3]) - max(a[1], b[1])
    if iw <= 0 or ih <= 0:
        return 0.0
    menor = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return iw * ih / menor if menor > 0 else 0.0


def em_duvida(deteccoes):
    """deteccoes: lista de (classe, confianca, (x1, y1, x2, y2)).
    True se o modelo esta em duvida sobre alguma classe de interesse."""
    baixo, alto = FAIXA_DUVIDA
    for c, conf, _ in deteccoes:
        if c in CLASSES_INTERESSE and baixo <= conf <= alto:
            return True
    maos = [b for c, _, b in deteccoes if c == MAO]
    luvas = [b for c, _, b in deteccoes if c == LUVA]
    return any(sobreposicao(m, l) >= 0.5 for m in maos for l in luvas)  # luva E mao nua no mesmo lugar


def detectar(model, frame):
    """Roda o modelo. Retorna (deteccoes, linhas_de_label_yolo)."""
    r = model(frame, device=0, imgsz=640, conf=CONF_MIN, classes=list(NOMES), verbose=False)[0]
    classes = r.boxes.cls.tolist()
    confs = r.boxes.conf.tolist()
    xyxy = r.boxes.xyxy.tolist()
    xywhn = r.boxes.xywhn.tolist()
    deteccoes, labels = [], []
    for c, cf, caixa, (x, y, w, h) in zip(classes, confs, xyxy, xywhn):
        c = int(c)
        deteccoes.append((c, cf, tuple(caixa)))
        if cf >= CONF_PRE:
            labels.append(f"{c} {x:.6f} {y:.6f} {w:.6f} {h:.6f}")
    return deteccoes, labels


def main():
    (PASTA / "images").mkdir(parents=True, exist_ok=True)
    if PRE_ANOTAR:
        (PASTA / "labels_pre").mkdir(parents=True, exist_ok=True)

    model = None
    if MODO == "incerteza" or PRE_ANOTAR:
        from ultralytics import YOLO  # importado aqui: o modo "intervalo" puro nao precisa do modelo
        model = YOLO(MODELO)

    eh_camera = isinstance(FONTE, int)
    eh_stream = str(FONTE).lower().startswith(("http://", "https://", "rtsp://"))  # ex.: celular via Wi-Fi
    ao_vivo = eh_camera or eh_stream
    cap = cv2.VideoCapture(FONTE, cv2.CAP_DSHOW) if eh_camera else cv2.VideoCapture(str(FONTE))
    if not cap.isOpened():
        raise SystemExit("Nao consegui abrir a fonte de video. Confira a camera ou o caminho do arquivo.")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    prefixo = "cam" if ao_vivo else Path(str(FONTE)).stem

    salvos, idx = 0, 0
    ultimo_salvo = ultimo_base = -1e9
    while salvos < MAX_QUADROS:
        ok, frame = cap.read()
        if not ok:  # fim do video ou falha da camera
            break
        idx += 1
        t = time.time() if ao_vivo else idx / fps  # video gravado: usa o tempo do proprio video

        deteccoes, labels = detectar(model, frame) if model is not None else ([], [])

        if MODO == "intervalo":
            salvar = (t - ultimo_salvo) >= INTERVALO_S
        else:
            salvar = ((t - ultimo_salvo) >= INTERVALO_S and em_duvida(deteccoes)) or \
                     (INTERVALO_BASE_S > 0 and (t - ultimo_base) >= INTERVALO_BASE_S)

        if salvar:
            nome = f"{prefixo}_{datetime.now():%Y%m%d_%H%M%S}_{idx:06d}"
            cv2.imwrite(str(PASTA / "images" / f"{nome}.jpg"), frame)  # quadro limpo, sem caixas
            if PRE_ANOTAR and model is not None:
                (PASTA / "labels_pre" / f"{nome}.txt").write_text("\n".join(labels))
            ultimo_salvo = ultimo_base = t
            salvos += 1

        if MOSTRAR:
            vis = frame.copy()
            cv2.putText(vis, f"salvos: {salvos}/{MAX_QUADROS}  modo: {MODO}", (10, 28),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 200, 0), 2)
            cv2.imshow("Captura - aperte q para parar", vis)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

    cap.release()
    cv2.destroyAllWindows()
    print(f"Pronto: {salvos} quadros salvos em {PASTA}")


if __name__ == "__main__":
    main()

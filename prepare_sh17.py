"""
Prepara o SH17 para treino com Ultralytics:
  - redimensiona as imagens (lado maior = 1280 px); os labels YOLO sao normalizados,
    entao continuam validos sem alteracao
  - separa treino/val usando as duas listas .txt da raiz do dataset
  - gera data.yaml, um resumo de instancias por classe e imagens de conferencia

Uso (com o ambiente virtual ativado):
    python prepare_sh17.py
    python prepare_sh17.py "C:\\origem\\sh17" "C:\\destino\\sh17_1280"

Pode ser interrompido e executado de novo: o que ja foi convertido e pulado.
"""
import os
import random
import shutil
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import cv2

SRC = Path(sys.argv[1] if len(sys.argv) > 1 else r"C:\projetos\epi\sh17")
DST = Path(sys.argv[2] if len(sys.argv) > 2 else r"C:\projetos\epi\sh17_1280")
MAX_SIDE = 1280

# Ordem dos IDs conforme o yaml oficial do SH17 (versao Kaggle).
# ATENCAO: difere da ordem da lista de classes do README.
NAMES = [
    "person", "ear", "ear-mufs", "face", "face-guard", "face-mask", "foot", "tool",
    "glasses", "gloves", "helmet", "hands", "head", "medical-suit", "shoes",
    "safety-suit", "safety-vest",
]
CHECK_CLASSES = {8, 9, 10, 16}  # glasses, gloves, helmet, safety-vest


def convert(job):
    src_img, dst_img, src_lbl, dst_lbl = job
    if os.path.exists(dst_img) and os.path.exists(dst_lbl):
        return None
    img = cv2.imread(src_img)
    if img is None:
        return f"imagem ilegivel: {src_img}"
    h, w = img.shape[:2]
    scale = MAX_SIDE / max(h, w)
    if scale < 1:
        img = cv2.resize(img, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
    cv2.imwrite(dst_img, img, [cv2.IMWRITE_JPEG_QUALITY, 95])
    if os.path.exists(src_lbl):
        shutil.copyfile(src_lbl, dst_lbl)
    else:
        Path(dst_lbl).write_text("")  # imagem sem objetos
    return None


def read_list(txt, imgs):
    stems, missing = [], 0
    for line in txt.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        stem = Path(line.replace("\\", "/")).stem
        if stem in imgs:
            stems.append(stem)
        else:
            missing += 1
    return stems, missing


def class_summary():
    cnt = Counter()
    for lbl in (DST / "labels").rglob("*.txt"):
        for line in lbl.read_text().splitlines():
            if line.strip():
                cnt[int(line.split()[0])] += 1
    print("\nInstancias por classe (total esperado ~75.994):")
    for cid in sorted(cnt):
        name = NAMES[cid] if cid < len(NAMES) else "??? ID FORA DA LISTA"
        print(f"  {cid:2d} {name:14s} {cnt[cid]}")
    print(f"  total: {sum(cnt.values())}")


def draw_checks(n=8):
    random.seed(0)
    cands = []
    for lbl in (DST / "labels" / "val").glob("*.txt"):
        ids = {int(l.split()[0]) for l in lbl.read_text().splitlines() if l.strip()}
        if ids & CHECK_CLASSES:
            cands.append(lbl)
    out = DST / "check"
    out.mkdir(exist_ok=True)
    for lbl in random.sample(cands, min(n, len(cands))):
        img = cv2.imread(str(DST / "images" / "val" / f"{lbl.stem}.jpg"))
        h, w = img.shape[:2]
        for line in lbl.read_text().splitlines():
            if not line.strip():
                continue
            c, x, y, bw, bh = line.split()[:5]
            c, x, y, bw, bh = int(c), float(x), float(y), float(bw), float(bh)
            p1 = (int((x - bw / 2) * w), int((y - bh / 2) * h))
            p2 = (int((x + bw / 2) * w), int((y + bh / 2) * h))
            cv2.rectangle(img, p1, p2, (0, 255, 0), 2)
            cv2.putText(img, NAMES[c], (p1[0], max(p1[1] - 6, 14)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        cv2.imwrite(str(out / f"{lbl.stem}.jpg"), img)
    print(f"\nImagens de conferencia salvas em: {out}")


def main():
    lists = sorted(SRC.glob("*.txt"))
    print("Listas encontradas na raiz:", [p.name for p in lists])
    train_txt = [p for p in lists if "train" in p.name.lower()]
    val_txt = [p for p in lists if p not in train_txt and ("val" in p.name.lower() or "test" in p.name.lower())]
    if len(train_txt) != 1 or len(val_txt) != 1:
        sys.exit("Nao consegui identificar as listas de treino/val. Me mande os nomes acima.")

    imgs = {p.stem: p for p in (SRC / "images").iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png"}}
    print(f"Imagens encontradas: {len(imgs)}")

    jobs = []
    for split, txt in (("train", train_txt[0]), ("val", val_txt[0])):
        stems, missing = read_list(txt, imgs)
        print(f"{split} ({txt.name}): {len(stems)} imagens, {missing} da lista sem arquivo correspondente")
        (DST / "images" / split).mkdir(parents=True, exist_ok=True)
        (DST / "labels" / split).mkdir(parents=True, exist_ok=True)
        for s in stems:
            jobs.append((
                str(imgs[s]), str(DST / "images" / split / f"{s}.jpg"),
                str(SRC / "labels" / f"{s}.txt"), str(DST / "labels" / split / f"{s}.txt"),
            ))

    workers = max(1, (os.cpu_count() or 2) - 1)
    print(f"Convertendo {len(jobs)} imagens com {workers} processos...")
    errors = 0
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for i, err in enumerate(ex.map(convert, jobs, chunksize=16), 1):
            if err:
                errors += 1
                print("ERRO:", err)
            if i % 500 == 0:
                print(f"  {i}/{len(jobs)}")
    print(f"Conversao concluida ({errors} erros).")

    yaml = f"path: {DST.as_posix()}\ntrain: images/train\nval: images/val\nnames:\n"
    yaml += "".join(f"  {i}: {n}\n" for i, n in enumerate(NAMES))
    (DST / "data.yaml").write_text(yaml)
    print(f"data.yaml salvo em: {DST / 'data.yaml'}")

    class_summary()
    draw_checks()


if __name__ == "__main__":
    main()

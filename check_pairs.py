"""
Confere, nos labels do SH17, se a caixa do EPI aparece junto da caixa da parte do corpo.
Exemplo: quando existe um "helmet", tambem existe um "head" no mesmo lugar?

Uso (com o ambiente virtual ativado):
    python check_pairs.py
"""
import sys
from pathlib import Path

LABELS = Path(sys.argv[1] if len(sys.argv) > 1 else r"C:\projetos\epi\sh17_1280\labels\train")
LIMIAR = 0.5  # fracao da menor caixa que precisa estar coberta para contar como "no mesmo lugar"

# (id do EPI, id da parte do corpo, descricao)
PARES = [
    (10, 12, "helmet  x head"),
    (9, 11, "gloves  x hands"),
    (8, 3, "glasses x face"),
]


def carregar(arquivo):
    caixas = []
    for linha in arquivo.read_text().splitlines():
        partes = linha.split()
        if len(partes) < 5:
            continue
        c, x, y, w, h = int(partes[0]), *map(float, partes[1:5])
        caixas.append((c, x - w / 2, y - h / 2, x + w / 2, y + h / 2))
    return caixas


def sobreposicao(a, b):
    """Area da intersecao dividida pela area da MENOR das duas caixas."""
    iw = min(a[3], b[3]) - max(a[1], b[1])
    ih = min(a[4], b[4]) - max(a[2], b[2])
    if iw <= 0 or ih <= 0:
        return 0.0
    menor = min((a[3] - a[1]) * (a[4] - a[2]), (b[3] - b[1]) * (b[4] - b[2]))
    return (iw * ih) / menor if menor > 0 else 0.0


def main():
    arquivos = list(LABELS.glob("*.txt"))
    if not arquivos:
        sys.exit(f"Nenhum label encontrado em {LABELS}")
    total = {p[2]: 0 for p in PARES}
    com_par = {p[2]: 0 for p in PARES}
    for arq in arquivos:
        caixas = carregar(arq)
        for epi_id, corpo_id, nome in PARES:
            corpos = [c for c in caixas if c[0] == corpo_id]
            for epi in (c for c in caixas if c[0] == epi_id):
                total[nome] += 1
                if any(sobreposicao(epi, c) >= LIMIAR for c in corpos):
                    com_par[nome] += 1
    print(f"Labels analisados: {len(arquivos)} arquivos em {LABELS}\n")
    for _, _, nome in PARES:
        n = total[nome]
        pct = 100 * com_par[nome] / n if n else 0
        print(f"{nome}: {n} caixas de EPI, {com_par[nome]} ({pct:.0f}%) com caixa do corpo no mesmo lugar")


if __name__ == "__main__":
    main()

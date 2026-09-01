# 🛡️ Aegis — Sistema de Visão para Segurança Industrial (AIoT)

> Sistema de segurança industrial que integra Inteligência Artificial e Automação (AIoT) para prevenção de acidentes. O Aegis identifica em tempo real a ausência de Equipamentos de Proteção Individual (EPI) através de visão computacional e intercepta automaticamente o funcionamento da máquina, em conformidade com os princípios da NR-12.

**Status:** 🚧 Em desenvolvimento — fase atual: validação do módulo de visão computacional

---

## 📌 Sobre o projeto

O sistema funciona em um pipeline contínuo:

```
Câmera IP/Webcam → YOLO (Python/OpenCV) → Janela de Tolerância → Modbus TCP → CLP (Ladder) → Fail-Safe → Contator → Máquina
                                                                                      ↑
                                                                        Botoeira de Emergência (NR-12)
```

## 🧩 Componentes do sistema

| Componente | Função |
|---|---|
| Câmera IP / Webcam | Captura o fluxo de vídeo contínuo da área de trabalho |
| YOLO (Python/OpenCV) | Detecta o operador e verifica presença de EPIs em tempo real |
| Janela de tolerância temporal | Evita falsos disparos por oclusão rápida ou variação de iluminação |
| Rede Ethernet & Switch | Comunicação via Modbus TCP entre o computador e o CLP |
| CLP | Roda lógica Ladder, monitora status da IA, aciona parada Fail-Safe |
| Botoeira de Emergência (NR-12) | Corte manual direto, independente do processamento de IA |
| Quadro elétrico (trilho DIN) | Organiza e protege a distribuição de energia (24V/220V) |
| Contator industrial | Corta a alimentação principal da máquina |
| Botoeira de reset | Exige validação manual da área antes de religar |
| Sinalizador visual/sonoro | Feedback imediato do status da célula (seguro/parada) |

## 🗺️ Roadmap

- [x] Definição da arquitetura do sistema
- [ ] Ambiente Python configurado (Ultralytics YOLO + OpenCV)
- [ ] Teste de detecção genérica (pessoa) via webcam
- [ ] Dataset de EPI (Roboflow ou anotação própria)
- [ ] Fine-tuning do modelo YOLO para classes de EPI
- [ ] Implementação da janela de tolerância temporal
- [ ] Validação da detecção em cenário controlado
- [ ] Aquisição dos componentes elétricos/industriais
- [ ] Lógica Ladder no CLP
- [ ] Integração Python ↔ CLP via Modbus TCP
- [ ] Montagem do quadro elétrico e circuito de segurança
- [ ] Testes integrados de ponta a ponta

## 📅 Log de desenvolvimento

| Data | Atualização |
|---|---|
| _(01/09/2026)_ | Criação do repositório e definição da arquitetura |

---

> Este é um projeto autoral em desenvolvimento, documentado publicamente para fins de aprendizado e portfólio técnico.

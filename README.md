# Tesouraria As a Service — Grupo Botuverá

App em Streamlit para consolidação diária das posições de tesouraria.

## Como atualizar diariamente

1. Exporte as posições da XP no mesmo formato `Posição Consolidada - <conta>.xlsx`.
2. Substitua os arquivos dentro da pasta `data/positions/`.
3. Faça commit e push no GitHub.
4. O Streamlit Cloud redeploya automaticamente e o app sensibiliza os dados.

## Rodar localmente

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Personalização rápida

- Titulares: edite `data/config/clientes.csv`.
- Política e limites: ajustar constantes no começo do `app.py`.
- Visual: alterar bloco CSS em `inject_css()`.

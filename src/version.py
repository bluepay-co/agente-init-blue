"""Fonte única da versão do Agente Blue.

Consumida por:
  • agente.py       → log de inicialização e rota /api/version
  • web/index.html  → rodapé do cabeçalho (preenchido em runtime por script.js)
  • build/*.spec    → metadados do executável

Ao lançar uma versão, altere APENAS aqui e registre a mudança no CHANGELOG.md.
"""

__version__ = "4.0.0"

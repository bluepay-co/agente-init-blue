# Makefile — Agente Blue
# Atalhos para compilar o agente. Requer Python + PyInstaller instalados.
# (No Windows, precisa do GNU Make. Sem make, rode os comandos powershell direto.)
#
# Uso rapido:
#   make            -> compila ONEDIR  (dev/testes)   dist\AgenteBlue\AgenteBlue.exe
#   make onefile    -> compila ONEFILE (entrega)      dist\AgenteBlue.exe
#   make assets     -> atualiza web/, assets/ e installers/ sem recompilar
#   make run        -> executa o agente compilado (onedir)
#   make dev        -> roda direto do codigo-fonte, sem compilar
#   make clean      -> remove dist/ e __pycache__

PS = powershell -ExecutionPolicy Bypass
COMPILAR = build/compilar.ps1

.PHONY: build onefile assets run dev clean help

build:
	$(PS) -File $(COMPILAR)

onefile:
	$(PS) -File $(COMPILAR) -Onefile

assets:
	$(PS) -File $(COMPILAR) -AssetsOnly

run:
	$(PS) -Command "Start-Process dist\AgenteBlue\AgenteBlue.exe"

dev:
	python src/agente.py

clean:
	$(PS) -Command "Remove-Item -Recurse -Force dist,src\__pycache__ -ErrorAction SilentlyContinue"

help:
	@echo make          - compila ONEDIR  (dev)      dist\AgenteBlue\
	@echo make onefile  - compila ONEFILE (entrega)  dist\AgenteBlue.exe
	@echo make assets   - atualiza web/assets/installers sem recompilar
	@echo make run      - executa o agente (onedir)
	@echo make dev      - roda do codigo-fonte
	@echo make clean    - remove dist e caches

# -*- mode: python ; coding: utf-8 -*-
# AgenteBlue.spec — suporta DOIS modos de build:
#
#   ONEDIR  (padrao):  .\build\compilar.ps1
#       -> dist\AgenteBlue\AgenteBlue.exe + _internal\  (sem extracao / sem _MEI)
#          web\\ e assets\\ ficam SOLTOS ao lado (editaveis sem recompilar).
#
#   ONEFILE (entrega): .\build\compilar.ps1 -Onefile   (define env AGENTEBLUE_ONEFILE=1)
#       -> dist\AgenteBlue.exe  (arquivo unico, comodo pro pendrive).
#          web\\, assets\\ e installers\\ ficam EMBUTIDOS no exe.

import glob
import os

# Raiz do projeto: este .spec vive em build\, um nivel abaixo da raiz.
ROOT = os.path.abspath(os.path.join(SPECPATH, '..'))
SRC = os.path.join(ROOT, 'src')

ONEFILE = os.environ.get('AGENTEBLUE_ONEFILE') == '1'
# Por padrao o config.json NAO e embutido: ele guarda senhas e o executavel
# e distribuido. Para embutir mesmo assim (entrega em pendrive, sem arquivo
# solto), defina AGENTEBLUE_EMBED_CONFIG=1 ciente de que as credenciais
# passam a viajar dentro do .exe.
EMBED_CONFIG = os.environ.get('AGENTEBLUE_EMBED_CONFIG') == '1'

datas = []

# Interface (web\) e icone/wallpaper (assets\) mantem a mesma estrutura de
# pastas dentro do bundle; no onedir servem de fallback para as copias soltas.
for _f in glob.glob(os.path.join(ROOT, 'web', '*')):
    if os.path.isfile(_f):
        datas.append((_f, 'web'))
for _f in glob.glob(os.path.join(ROOT, 'assets', '*')):
    if os.path.isfile(_f):
        datas.append((_f, 'assets'))

# Instaladores de installers\ embutidos (build autossuficiente: um unico
# AgenteBlue.exe instala tudo). Em runtime o codigo procura primeiro uma pasta
# installers\ ao lado do exe e usa esta copia como fallback.
for _f in glob.glob(os.path.join(ROOT, 'installers', '*.exe')):
    datas.append((_f, 'installers'))

if EMBED_CONFIG:
    _cfg = os.path.join(ROOT, 'config.json')
    if os.path.exists(_cfg):
        print('[spec] AVISO: config.json sera EMBUTIDO no executavel (contem senhas).')
        datas.append((_cfg, '.'))

_icon = os.path.join(ROOT, 'assets', 'bluepay-ico.ico')
icon_file = _icon if os.path.exists(_icon) else None

a = Analysis(
    [os.path.join(SRC, 'agente.py')],
    pathex=[SRC],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

if ONEFILE:
    # Tudo dentro de um unico .exe.
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name='AgenteBlue',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=icon_file,
        uac_admin=False,
    )
else:
    # Onedir: exe leve + pasta _internal ao lado.
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name='AgenteBlue',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=icon_file,
        uac_admin=False,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=True,
        upx_exclude=[],
        name='AgenteBlue',
    )

"""Verifica que senhas do config.json nunca aparecem em log nem ficam em disco.

Roda em qualquer sistema: nao executa etapas do setup.

    python tests/test_segredos.py
"""
import os, sys, tempfile, pathlib
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
base = tempfile.mkdtemp(); os.environ["ProgramData"] = base
sys.path.insert(0, os.path.join(RAIZ, "src"))
import agente

falhas = 0
def checa(desc, ok):
    global falhas; falhas += not ok
    print(f"  {'OK   ' if ok else 'FALHA'}  {desc}")

# Simula um config.json carregado com senhas reais.
agente._ADMIN_PASS = "Sham23*"
agente.USUARIOS = [{"nome": "Suporte", "senha": "SenhaDoSuporte1"}]
agente.SOFTWARES = [{"nome": "AnyDesk", "unattended_password": "SenhaAnyDesk9"}]

capturado = []
agente._broadcast = lambda ev: capturado.append(ev.get("msg", ""))

agente._log("rodando: net use \\\\srv /user:admin Sham23*")
agente._log("usuario Suporte com senha SenhaDoSuporte1 criado")
agente._log("anydesk --set-password SenhaAnyDesk9")
texto = "\n".join(capturado)
checa("senha do admin mascarada no log",     "Sham23*" not in texto)
checa("senha de usuario mascarada no log",   "SenhaDoSuporte1" not in texto)
checa("senha do AnyDesk mascarada no log",   "SenhaAnyDesk9" not in texto)
checa("mensagem preservada fora do segredo", "net use" in texto and "***" in texto)

# Arquivo de log em disco tambem nao pode conter a senha.
conteudo_log = pathlib.Path(agente._LOG_FILE).read_text(encoding="utf-8", errors="replace")
checa("arquivo de log sem a senha", "Sham23*" not in conteudo_log)

# Gravacao protegida + remocao garantida.
alvo = pathlib.Path(base) / "teste_credencial.ps1"
agente._gravar_script_protegido(alvo, "$senha = 'Sham23*'\n")
checa("script criado", alvo.exists())
agente._apagar_script(alvo)
checa("script apagado apos o uso", not alvo.exists())

print("\nRESULTADO:", "tudo certo" if not falhas else f"{falhas} falha(s)")
sys.exit(1 if falhas else 0)

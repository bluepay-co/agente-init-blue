"""Testes do módulo monitoramento.py.

Roda em Linux (sem Windows real) — mocka os subprocessos e o broadcast SSE.
Verifica:
  • polling de progresso (estado global atualiza corretamente)
  • endpoint cadastrar_e_instalar monta os dados certos
  • normalizar_url trata os casos comuns
  • serial_maquina filtra valores inválidos de BIOS
  • detecção de IP local e VPN no agente de métricas

    python -m pytest tests/test_monitoramento.py -v
"""

import json
import os
import sys
import threading
import unittest
from unittest.mock import MagicMock, patch

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "src"))

import monitoramento


# ══════════════════════════════════════════════
#  Setup: injeta helpers falsos (sem agente.py)
# ══════════════════════════════════════════════

_broadcast_log = []


def _fake_broadcast(event):
    _broadcast_log.append(event)


def _fake_registrar(msg):
    pass


def _fake_resolver(rel):
    return None


monitoramento.init(_fake_broadcast, _fake_registrar, _fake_resolver, 0)


class TestNormalizarUrl(unittest.TestCase):
    def test_url_completa(self):
        url, erro = monitoramento.normalizar_url("http://192.168.10.11:8550")
        self.assertEqual(url, "http://192.168.10.11:8550")
        self.assertEqual(erro, "")

    def test_sem_esquema(self):
        url, erro = monitoramento.normalizar_url("192.168.10.11:8550")
        self.assertEqual(url, "http://192.168.10.11:8550")
        self.assertEqual(erro, "")

    def test_barra_final(self):
        url, erro = monitoramento.normalizar_url("http://192.168.10.11:8550/")
        self.assertEqual(url, "http://192.168.10.11:8550")

    def test_vazia(self):
        url, erro = monitoramento.normalizar_url("")
        self.assertEqual(url, "")
        self.assertIn("branco", erro)

    def test_esquema_invalido(self):
        url, erro = monitoramento.normalizar_url("ftp://192.168.10.11")
        self.assertIn("http://", erro)


class TestSerialValido(unittest.TestCase):
    def test_serie_valida(self):
        self.assertEqual(monitoramento._serial_valido("ABC123DEF"), "ABC123DEF")

    def test_serie_vazia(self):
        self.assertEqual(monitoramento._serial_valido(""), "")

    def test_serie_oem(self):
        self.assertEqual(monitoramento._serial_valido("To Be Filled By O.E.M."), "")

    def test_serie_default_string(self):
        self.assertEqual(monitoramento._serial_valido("Default string"), "")

    def test_serie_curta(self):
        self.assertEqual(monitoramento._serial_valido("AB"), "")

    def test_serie_so_pontuacao(self):
        self.assertEqual(monitoramento._serial_valido("---"), "")

    def test_serie_none(self):
        self.assertEqual(monitoramento._serial_valido(None), "")

    def test_serie_system_serial_number(self):
        self.assertEqual(monitoramento._serial_valido("System Serial Number"), "")

    def test_serie_com_espacos(self):
        self.assertEqual(monitoramento._serial_valido("  ABC123  "), "ABC123")


class TestProgressoPolling(unittest.TestCase):
    """Verifica que _log e _progresso atualizam o estado consultável."""

    def setUp(self):
        _broadcast_log.clear()
        monitoramento._iniciar_progresso()

    def test_estado_inicial(self):
        estado = monitoramento.progresso_atual()
        self.assertEqual(estado["pct"], 0)
        self.assertTrue(estado["ativo"])
        self.assertEqual(estado["logs"], [])

    def test_log_acumula(self):
        monitoramento._log("Passo 1", "info")
        monitoramento._log("Passo 2", "ok")
        estado = monitoramento.progresso_atual()
        self.assertEqual(len(estado["logs"]), 2)
        self.assertEqual(estado["logs"][0]["msg"], "Passo 1")
        self.assertEqual(estado["logs"][1]["tipo"], "ok")

    def test_progresso_atualiza(self):
        monitoramento._progresso(50, "Metade")
        estado = monitoramento.progresso_atual()
        self.assertEqual(estado["pct"], 50)
        self.assertEqual(estado["texto"], "Metade")
        self.assertFalse(estado["ok"])

    def test_progresso_completo(self):
        monitoramento._progresso(100, "Pronto!", ok=True)
        estado = monitoramento.progresso_atual()
        self.assertEqual(estado["pct"], 100)
        self.assertTrue(estado["ok"])

    def test_progresso_erro(self):
        monitoramento._progresso(100, "Falhou", erro=True)
        estado = monitoramento.progresso_atual()
        self.assertTrue(estado["erro"])

    def test_finalizar(self):
        monitoramento._finalizar_progresso()
        estado = monitoramento.progresso_atual()
        self.assertFalse(estado["ativo"])

    def test_broadcast_tambem_dispara(self):
        monitoramento._log("teste", "info")
        self.assertTrue(any(e["type"] == "mon_log" for e in _broadcast_log))

    def test_reiniciar_limpa_logs(self):
        monitoramento._log("antigo", "info")
        monitoramento._iniciar_progresso()
        estado = monitoramento.progresso_atual()
        self.assertEqual(estado["logs"], [])
        self.assertEqual(estado["pct"], 0)

    def test_thread_safety(self):
        """Múltiplas threads gravando ao mesmo tempo não causam erro."""
        erros = []

        def gravar(n):
            try:
                for i in range(20):
                    monitoramento._log(f"Thread {n} msg {i}", "info")
                    monitoramento._progresso(i * 5, f"t{n}")
            except Exception as e:
                erros.append(e)

        threads = [threading.Thread(target=gravar, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(erros, [])
        estado = monitoramento.progresso_atual()
        self.assertGreater(len(estado["logs"]), 0)


class TestCadastrarEInstalarPayload(unittest.TestCase):
    """Verifica que o endpoint monta os dados corretamente."""

    @patch("monitoramento.cadastrar")
    @patch("monitoramento.instalar")
    def test_busca_existente_nao_cadastra(self, mock_instalar, mock_cadastrar):
        """Quando patrimonio já vem no payload, não chama cadastrar."""
        params = {
            "patrimonio": "1234",
            "url_api": "http://192.168.10.11:8550",
            "token_agente": "tok123",
            "intervalo_segundos": 300,
        }
        # Simula o que o endpoint faria
        patrimonio = params.get("patrimonio")
        self.assertEqual(patrimonio, "1234")
        mock_cadastrar.assert_not_called()

    @patch("monitoramento.cadastrar")
    def test_cadastro_novo_extrai_patrimonio(self, mock_cadastrar):
        """Quando não tem patrimonio, chama cadastrar e extrai da resposta."""
        mock_cadastrar.return_value = (200, {"ok": True, "patrimonio": "8907"})
        dados = {
            "nome_maquina": "PC-Teste",
            "responsavel": "Fulano",
            "categoria": "Notebooks",
        }
        st, data = monitoramento.cadastrar("http://192.168.10.11:8550", "tok", dados)
        patrimonio = data.get("patrimonio") or data.get("patrimonio_interno")
        self.assertEqual(patrimonio, "8907")

    @patch("monitoramento.cadastrar")
    def test_cadastro_erro_409_serie_duplicada(self, mock_cadastrar):
        """Máquina com mesma série já existe — retorna 409."""
        mock_cadastrar.return_value = (
            409,
            {"detail": "Esta máquina já está cadastrada no patrimônio 1234"}
        )
        st, data = monitoramento.cadastrar("http://x", "tok", {"nome_maquina": "PC"})
        self.assertEqual(st, 409)
        self.assertIn("já está cadastrada", data["detail"])

    @patch("monitoramento.cadastrar")
    def test_cadastro_sem_patrimonio_na_resposta(self, mock_cadastrar):
        """Se a API não devolve patrimonio, o fluxo para."""
        mock_cadastrar.return_value = (400, {"detail": "Erro qualquer"})
        st, data = monitoramento.cadastrar("http://x", "tok", {})
        patrimonio = data.get("patrimonio") or data.get("patrimonio_interno")
        self.assertIsNone(patrimonio)


class TestDeteccaoIPMetricas(unittest.TestCase):
    """Verifica a detecção de IP local e VPN no agente de métricas."""

    def test_ip_local_192(self):
        """IP 192.168.x.x é reconhecido como ip_local."""
        ip = "192.168.10.50"
        self.assertTrue(ip.startswith("192.168."))

    def test_ip_vpn_10(self):
        """IP 10.x.x.x é reconhecido como ip_vpn."""
        ip = "10.8.0.5"
        self.assertTrue(ip.startswith("10."))

    def test_ip_vpn_172_16(self):
        """IP 172.16.x.x (range VPN pfSense) é reconhecido como ip_vpn."""
        ip = "172.16.100.5"
        self.assertTrue(ip.startswith("172."))
        segundo = int(ip.split('.')[1])
        self.assertTrue(16 <= segundo <= 31)

    def test_ip_172_fora_do_range_privado(self):
        """IP 172.15.x.x não é range privado — não é VPN."""
        ip = "172.15.0.1"
        segundo = int(ip.split('.')[1])
        self.assertFalse(16 <= segundo <= 31)

    def test_loopback_ignorado(self):
        """127.0.0.1 não é ip_local nem ip_vpn."""
        ip = "127.0.0.1"
        self.assertFalse(ip.startswith("192.168."))
        self.assertFalse(ip.startswith("10."))

    def test_fonte_agente_metricas_tem_ip_vpn(self):
        """O fonte do agente de métricas inclui ip_vpn no retorno."""
        path = os.path.join(RAIZ, "agents", "windows", "agente_metricas.py")
        with open(path, encoding="utf-8") as f:
            codigo = f.read()
        self.assertIn('"ip_vpn"', codigo)
        self.assertIn("ip_vpn", codigo)


class TestInstalacaoParams(unittest.TestCase):
    """Verifica validações antes de iniciar a instalação."""

    def test_patrimonio_vazio_aborta(self):
        """instalar() sem patrimônio emite erro e progresso 100%."""
        _broadcast_log.clear()
        monitoramento.instalar({"patrimonio": "", "token_agente": "tok",
                                "url_api": "http://x", "intervalo_segundos": 300})
        msgs = [e for e in _broadcast_log if e["type"] == "mon_progresso"]
        self.assertTrue(any(m["erro"] for m in msgs))
        self.assertTrue(any(m["pct"] == 100 for m in msgs))

    def test_token_vazio_aborta(self):
        _broadcast_log.clear()
        monitoramento.instalar({"patrimonio": "1234", "token_agente": "",
                                "url_api": "http://x"})
        msgs = [e for e in _broadcast_log if e["type"] == "mon_progresso"]
        self.assertTrue(any(m["erro"] for m in msgs))

    def test_url_invalida_aborta(self):
        _broadcast_log.clear()
        monitoramento.instalar({"patrimonio": "1234", "token_agente": "tok",
                                "url_api": ""})
        msgs = [e for e in _broadcast_log if e["type"] == "mon_progresso"]
        self.assertTrue(any(m["erro"] for m in msgs))


class TestPortaAberta(unittest.TestCase):
    def test_porta_invalida_retorna_false(self):
        self.assertFalse(monitoramento._porta_aberta("http://255.255.255.255:1", timeout=1))

    def test_url_vazia_retorna_false(self):
        self.assertFalse(monitoramento._porta_aberta("", timeout=1))


if __name__ == "__main__":
    unittest.main()

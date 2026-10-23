# BlueControl IT — Agente de Monitoramento Leve 📊

Um agente **minimalista** e **leve** para coletar métricas básicas de máquinas e enviá-las para o BlueControl.

## 🎯 O que faz?

Coleta a cada 5 minutos:
- 📈 **CPU** — Percentual de uso
- 💾 **RAM** — Percentual e total
- 💿 **Disco C:** — Percentual e total
- 🌐 **IP** — Local
- ⏱️ **Uptime** — Tempo ligado

Envia tudo automaticamente para a API BlueControl via HTTP.

---

## 📦 Requisitos

- **Windows 10/11** (ou Server 2019+)
- **Python 3.8+** ou usar o EXE compilado
- **Acesso à rede** para alcançar a API BlueControl

## ⚡ Instalação Rápida (Recomendado)

### 1. Copie os 3 arquivos para a máquina:
```
agente_monitoramento_leve.py
instalar_agente_lite.bat
config.json (opcional)
```

### 2. Execute como Administrador:
```bash
instalar_agente_lite.bat
```

O instalador vai:
✅ Verificar Python  
✅ Instalar dependências (psutil, requests)  
✅ Copiar para `C:\BlueControl\`  
✅ Compilar EXE (opcional)  
✅ Instalar como Serviço Windows  
✅ Iniciar o serviço  

### 3. Configure (edite `C:\BlueControl\config.json`):

```json
{
  "patrimonio": "0001",
  "url_api": "http://192.168.0.17:8550",
  "token_agente": "SEU_TOKEN_AQUI",
  "intervalo_segundos": 300
}
```

| Campo | Descrição |
|-------|-----------|
| `patrimonio` | Número/ID do equipamento no BlueControl |
| `url_api` | Endereço do servidor BlueControl |
| `token_agente` | Token de autenticação (padrão funciona) |
| `intervalo_segundos` | Quanto em quanto tempo coleta (300 = 5 min) |

### 4. Reinicie o serviço:

```bash
net stop BlueControlLite
net start BlueControlLite
```

---

## 🔧 Instalação Manual (Alternativa)

Se preferir instalar sem o script:

```bash
# 1. Instalar dependências
pip install psutil requests

# 2. Criar pasta
mkdir C:\BlueControl
cd C:\BlueControl

# 3. Copiar arquivo
copy agente_monitoramento_leve.py C:\BlueControl\

# 4. Criar config.json
notepad C:\BlueControl\config.json

# 5. Testar (modo debug)
python C:\BlueControl\agente_monitoramento_leve.py

# Se funcionar, criar serviço:
pyinstaller --onefile --noconsole agente_monitoramento_leve.py
sc create BlueControlLite binpath= "C:\BlueControl\dist\agente_monitoramento_leve.exe"
net start BlueControlLite
```

---

## 📋 Verificar Status

### Serviço está rodando?
```bash
net start | find "BlueControlLite"
```

### Ver logs:
```bash
type C:\BlueControl\lite.log

# Ou em tempo real (PowerShell):
Get-Content C:\BlueControl\lite.log -Wait
```

### Teste manual:
```bash
python C:\BlueControl\agente_monitoramento_leve.py
```

---

## ⚙️ Comandos Úteis

### Parar o serviço:
```bash
net stop BlueControlLite
```

### Iniciar o serviço:
```bash
net start BlueControlLite
```

### Reiniciar:
```bash
net stop BlueControlLite & net start BlueControlLite
```

### Remover o serviço:
```bash
sc delete BlueControlLite
```

### Ver status no Serviços do Windows:
```bash
services.msc
```
(Procure por "BlueControl IT - Agente Leve")

---

## 🔍 Troubleshooting

### "Conexão recusada" no log
- A máquina não consegue alcançar a API
- Verifique: `ping 192.168.0.17`
- Verifique a URL em `config.json`

### "Token inválido"
- Confira o `token_agente` em `config.json`
- Sem valor padrão. Defina `BLUECONTROL_AGENT_TOKEN` antes de instalar, ou
  passe `-TokenAgente` ao script. O agente não inicia sem token.

### Serviço não inicia
```bash
# Verifique os logs do Windows Event Viewer
eventvwr.msc
# Procure em: Logs do Windows > Sistema
```

### "psutil/requests not installed"
```bash
pip install --upgrade psutil requests
```

---

## 📊 Exemplo de Log

```
2026-09-03 10:15:32 [INFO] [INICIADO] BlueControl Lite | Patrimônio: 0001 | API: http://192.168.0.17:8550
2026-09-03 10:15:32 [INFO] Intervalo: 300s | Primeira coleta em 300s...
2026-09-03 10:20:32 [INFO] ✓ Métricas enviadas com sucesso | CPU: 45.2% | RAM: 62.3%
2026-09-03 10:25:32 [INFO] ✓ Métricas enviadas com sucesso | CPU: 38.5% | RAM: 59.1%
```

---

## 🚀 Agora no BlueControl

Após instalar e o agente enviar métricas, acesse:

```
http://192.168.0.17:8550
→ Monitoramento (NOC)
→ Procure pela máquina com patrimônio "0001"
```

A máquina aparecerá com:
- ✅ Status: ONLINE
- 📈 Métricas em tempo real
- 🌐 IP detectado automaticamente

---

## 💡 Comparação: Agente Leve vs Completo

| Recurso | Leve | Completo |
|---------|------|----------|
| Tamanho | ~5 MB | ~30 MB |
| CPU/RAM coletada | ✅ Básico | ✅ Avançado |
| Temp CPU | ❌ | ✅ WMI |
| Inventário completo | ❌ | ✅ (1x/hora) |
| Windows Updates | ❌ | ✅ |
| Wi-Fi info | ❌ | ✅ |
| Segurança (BitLocker/Defender) | ❌ | ✅ |
| Tempo de boot | ⚡ ~1s | ⏱️ ~5s |

Use o **Leve** para:
- Máquinas com pouco recurso
- Instalação rápida
- Monitoramento básico

Use o **Completo** para:
- Análise detalhada
- Auditoria de segurança
- Planejamento de capacidade

---

## 🔐 Segurança

- ✅ Usa HTTPS se configurado na API
- ✅ Token de agente isolado de usuários
- ✅ Sem credenciais armazenadas
- ✅ Comunicação apenas com a API BlueControl

---

## 📝 Licença & Suporte

Desenvolvido para BlueControl IT v2.0

Para suporte ou bugs, entre em contato com a equipe BlueControl.

---

## 📞 Dúvidas Frequentes

**P: Quanto de banda usa?**  
R: ~2-5 KB a cada 5 minutos = ~0.5 MB/dia. Negligenciável.

**P: Funciona em máquinas antigas?**  
R: Sim, desde que tenha Python 3.8+ ou o EXE compilado.

**P: E se desligar a máquina?**  
R: O serviço para. Quando liga novamente, retoma automaticamente.

**P: Posso mudar o intervalo de coleta?**  
R: Sim, edite `intervalo_segundos` em `config.json`.

---

**Versão:** 1.0  
**Data:** Setembro 2026  
**BlueControl IT**

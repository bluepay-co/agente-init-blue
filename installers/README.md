# installers/

Instaladores usados pela etapa de **Softwares**. Cada entrada de `softwares`
no `config.json` aponta para um arquivo desta pasta pelo campo `filename`
(o nome precisa bater exatamente).

No build **onefile** o conteúdo daqui é embutido no `AgenteBlue.exe`. No
**onedir**, a pasta é copiada para o lado do executável — dá para trocar um
instalador sem recompilar.

## Arquivos versionados no Git

| Arquivo                          | Software              |
|----------------------------------|-----------------------|
| `Anydesk.exe`                    | AnyDesk               |
| `ChromeSetup.exe`                | Google Chrome         |
| `Reader_br_R9BVLF8K_install.exe` | Adobe Acrobat Reader  |
| `OfficeSetup.exe`                | Microsoft 365         |
| `winrar-x64-722br.exe`           | WinRAR                |

## Arquivos que você precisa baixar

Passam de 100 MB e por isso ficam fora do Git (veja o `.gitignore`). **Sem
eles, esses dois softwares falham na execução** — baixe antes de compilar:

| Arquivo                 | Software      | Origem                                                        |
|-------------------------|---------------|---------------------------------------------------------------|
| `GoogleDriveSetup.exe`  | Google Drive  | https://dl.google.com/drive-file-stream/GoogleDriveSetup.exe   |
| `SlackSetup.exe`        | Slack         | https://slack.com/downloads/windows                            |

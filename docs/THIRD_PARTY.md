# Ferramentas de terceiros

As ferramentas abaixo são gratuitas/open source. O código deste repositório não altera suas licenças.

| Ferramenta | Distribuição utilizada | Referência |
|---|---|---|
| Python/Tkinter | Python do usuário | https://www.python.org/ |
| Pillow | PyPI, versão em requirements.txt | https://python-pillow.org/ |
| zstandard | PyPI, versão em requirements.txt | https://github.com/indygreg/python-zstandard |
| truststore | PyPI, certificados TLS do sistema | https://github.com/sethmlarson/truststore |
| jpegtran/libjpeg-turbo 3.2.0 | Pacote Windows x64 conda-forge | https://github.com/libjpeg-turbo/libjpeg-turbo |
| Oxipng 10.2.1 | Release Windows x64 oficial | https://github.com/oxipng/oxipng |

Os binários não são incluídos no Git. `setup_tools.py` verifica hashes dos arquivos distribuídos e dos binários, preservando as licenças em `tools/licenses/`. URLs e SHA-256 estão em `tools-manifest.json`.

A API Cloud Files é componente do Windows: https://learn.microsoft.com/en-us/windows/win32/api/cfapi/nf-cfapi-cfgetplaceholderinfo

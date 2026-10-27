"""Baixa ferramentas Windows x64 fixadas e verifica SHA-256 antes de instalar localmente."""
from pathlib import Path
import hashlib, io, json, os, platform, shutil, tarfile, tempfile, urllib.request, zipfile
import zstandard
import ssl, truststore

BASE=Path(__file__).resolve().parent

def check(data, expected):
    if hashlib.sha256(data).hexdigest()!=expected: raise ValueError('SHA-256 não confere')

def write(data,path,expected=None):
    if expected:check(data,expected)
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.partial')
    with open(temp,'wb') as f:
        f.write(data);f.flush();os.fsync(f.fileno())
    os.replace(temp,path)

def main():
    if os.name!='nt' or platform.machine().lower() not in ('amd64','x86_64'):
        raise SystemExit('Esta distribuição requer Windows x64.')
    manifest=json.loads((BASE/'tools-manifest.json').read_text(encoding='utf-8'))
    hashes=manifest['binaries_sha256']
    with tempfile.TemporaryDirectory(prefix='comprovantes-tools-') as scratch:
        packages={}
        for key in ('jpegtran','oxipng'):
            item=manifest[key];print('Baixando',key,item['version'])
            request=urllib.request.Request(item['source'],headers={'User-Agent':'ComprovantesOptimizer/1.0'})
            context=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            with urllib.request.urlopen(request,timeout=120,context=context) as response:
                payload=response.read()
            check(payload,item['archive_sha256']);packages[key]=payload
        wanted={'Library/bin/jpegtran.exe','Library/bin/jpeg8.dll'}
        found=set()
        with zipfile.ZipFile(io.BytesIO(packages['jpegtran'])) as package:
            for name in package.namelist():
                if not name.endswith('.tar.zst'):continue
                with package.open(name) as archive:
                    with zstandard.ZstdDecompressor().stream_reader(archive) as stream:
                        with tarfile.open(fileobj=stream,mode='r|') as tar:
                            for member in tar:
                                if not member.isfile():continue
                                if member.name in wanted:
                                    data=tar.extractfile(member).read()
                                    dest=BASE/'tools/jpeg-modern'/member.name
                                    expected=hashes[str(dest.relative_to(BASE))]
                                    write(data,dest,expected);found.add(member.name)
                                elif member.name.startswith('info/licenses/'):
                                    # Só nome final; nenhuma extração livre de caminhos ou links.
                                    write(tar.extractfile(member).read(),BASE/'tools/licenses/libjpeg-turbo'/Path(member.name).name)
        if found!=wanted:raise ValueError('Binários JPEG ausentes no pacote')
        with zipfile.ZipFile(io.BytesIO(packages['oxipng'])) as package:
            found=False
            for name in package.namelist():
                filename=Path(name).name
                if filename=='oxipng.exe':
                    expected=next(v for k,v in hashes.items() if k.endswith('oxipng.exe'))
                    write(package.read(name),BASE/'tools/oxi/oxipng.exe',expected);found=True
                elif filename in ('LICENSE.txt','README.md'):
                    write(package.read(name),BASE/'tools/licenses/oxipng'/filename)
            if not found:raise ValueError('Oxipng ausente no pacote')
    print('Ferramentas verificadas e instaladas em tools/. Execute python otimizador.py.')

if __name__=='__main__':main()

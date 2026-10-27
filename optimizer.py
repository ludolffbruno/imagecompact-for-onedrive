"""Otimizador de imagens, Windows/OneDrive. Estado e backups ficam fora do OneDrive."""
from pathlib import Path
import os, sys, json, sqlite3, hashlib, shutil, subprocess, time, threading
import queue, csv, ctypes, struct, argparse, traceback, msvcrt
from PIL import Image, ImageChops, ImageStat
import math
from contextlib import closing
from perfis import PROFILES, databases, original_for, summarize

CODE_DIR = Path(__file__).resolve().parent
BASE = Path(os.environ.get('IMAGECOMPACT_DATA',
    str(Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'ImageCompactForOneDrive'))).resolve()
DEFAULT_ROOT = os.environ.get('IMAGECOMPACT_ROOT', str(Path.home() / 'OneDrive' / 'Imagens'))
JPEG = CODE_DIR / 'tools/jpeg-modern/Library/bin/jpegtran.exe'
PNG = CODE_DIR / 'tools/oxi/oxipng.exe'
HIDDEN = 0x08000000

def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''): h.update(block)
    return h.hexdigest()

def durable_copy(src, dst):
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(src, 'rb') as a, open(dst, 'wb') as b:
        shutil.copyfileobj(a, b, 1024 * 1024)
        b.flush()
        os.fsync(b.fileno())

def command(args, timeout=180):
    r = subprocess.run([str(x) for x in args], capture_output=True,
                       creationflags=HIDDEN, timeout=timeout)
    if r.returncode:
        raise RuntimeError(r.stderr.decode(errors='replace')[-1200:] or f'Codigo {r.returncode}')

def sync_state(path):
    """True/False/None, segundo o provedor Cloud Files do Windows (não Graph)."""
    from ctypes import wintypes as w
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    k.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p,
                             w.DWORD, w.DWORD, w.HANDLE]
    k.CreateFileW.restype = w.HANDLE
    k.CloseHandle.argtypes = [w.HANDLE]
    h = k.CreateFileW(str(path), 0x80, 7, None, 3, 0x00200000, None)
    if h == ctypes.c_void_p(-1).value: return None
    try:
        c = ctypes.WinDLL('cldapi')
        c.CfGetPlaceholderInfo.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                           w.DWORD, ctypes.POINTER(w.DWORD)]
        c.CfGetPlaceholderInfo.restype = ctypes.c_long
        buf = ctypes.create_string_buffer(65536)
        size = w.DWORD()
        result = c.CfGetPlaceholderInfo(h, 0, buf, len(buf), ctypes.byref(size))
        return struct.unpack_from('<i', buf.raw, 4)[0] == 1 if result == 0 else None
    finally:
        k.CloseHandle(h)

def jpeg_metadata(path):
    # Todos os marcadores APP0..APP15 e COM, incluindo EXIF, ICC e XMP.
    data = Path(path).read_bytes()
    if data[:2] != b'\xff\xd8': raise ValueError('JPEG invalido')
    pos, result = 2, []
    while pos < len(data):
        if data[pos] != 255: raise ValueError('Marcador JPEG invalido')
        while data[pos] == 255: pos += 1
        marker = data[pos]; pos += 1
        if marker in (0xda, 0xd9): break
        length = int.from_bytes(data[pos:pos+2], 'big')
        if length < 2: raise ValueError('Segmento JPEG invalido')
        if 0xe0 <= marker <= 0xef or marker == 0xfe:
            result.append((marker, data[pos+2:pos+length]))
        pos += length
    return sorted(result)

def png_metadata(path):
    data = Path(path).read_bytes(); pos = 8; result = []
    while pos + 12 <= len(data):
        size = int.from_bytes(data[pos:pos+4], 'big')
        tag = data[pos+4:pos+8]
        if tag not in (b'IHDR', b'IDAT', b'IEND', b'PLTE', b'tRNS'):
            result.append((tag, data[pos+8:pos+8+size]))
        pos += size + 12
    return sorted(result)

def validate(a, b):
    with Image.open(a) as x, Image.open(b) as y:
        if x.format != y.format or x.size != y.size: raise ValueError('Formato/dimensoes alterados')
        if getattr(x, 'n_frames', 1) != 1 or getattr(y, 'n_frames', 1) != 1:
            raise ValueError('Imagem animada/multiplas paginas: preservada')
        x.load(); y.load()
        mode = 'I' if x.mode.startswith('I') else ('CMYK' if x.mode == 'CMYK' else 'RGBA')
        if x.convert(mode).tobytes() != y.convert(mode).tobytes():
            raise ValueError('Pixels diferentes; original preservado')
        fn = jpeg_metadata if x.format == 'JPEG' else png_metadata
        if fn(a) != fn(b): raise ValueError('Metadados diferentes; original preservado')

def moderate_jpeg(src, dst, profile='moderado-v1'):
    settings=PROFILES[profile]
    with Image.open(src) as im:
        im.load()
        reference = im.copy()
        if settings['edge']:
            reference.thumbnail((settings['edge'],settings['edge']), Image.Resampling.LANCZOS)
        reference.save(dst, format='JPEG', quality=settings['quality'], subsampling=0,
                       optimize=True, progressive=True)
    # Mantem integralmente EXIF (inclusive orientacao), ICC, XMP e comentarios.
    original_markers = jpeg_metadata(src)
    data = dst.read_bytes(); pos = 2; body = bytearray()
    while pos < len(data):
        start = pos
        while data[pos] == 255: pos += 1
        marker = data[pos]; pos += 1
        if marker in (0xda,0xd9):
            body.extend(data[start:]); break
        length = int.from_bytes(data[pos:pos+2],'big')
        if not (0xe0 <= marker <= 0xef or marker == 0xfe):
            body.extend(data[start:pos+length])
        pos += length
    header = bytearray(b'\xff\xd8')
    for marker,payload in original_markers:
        header.extend(bytes([255,marker])+(len(payload)+2).to_bytes(2,'big')+payload)
    dst.write_bytes(header+body)
    with Image.open(dst) as reduced:
        reduced.load()
        if reduced.size != reference.size or reduced.format != 'JPEG':
            raise ValueError('Dimensoes/formato incorretos na verificacao moderada')
        diff = ImageChops.difference(reference.convert('RGB'),reduced.convert('RGB'))
        rms = math.sqrt(sum(x*x for x in ImageStat.Stat(diff).rms)/3)
        psnr = 99 if rms == 0 else 20*math.log10(255/rms)
        if psnr < settings['psnr']: raise ValueError(f'Qualidade abaixo do limite (PSNR {psnr:.1f} dB); original preservado')
    if jpeg_metadata(src) != jpeg_metadata(dst): raise ValueError('Metadados alterados')
    return True

def optimize(src, dst, profile='lossless'):
    if profile != 'lossless' and profile not in PROFILES: raise ValueError('Perfil desconhecido')
    with Image.open(src) as im:
        fmt = im.format
        if getattr(im, 'n_frames', 1) != 1: return False
        im.load()
    if fmt == 'JPEG':
        if profile in PROFILES: return moderate_jpeg(src,dst,profile)
        # Escolhe a menor entre codificacao sequencial e progressiva, sem requantizar.
        alt = dst.with_suffix('.alternate')
        try:
            command([JPEG, '-copy', 'all', '-optimize', '-outfile', dst, src])
            command([JPEG, '-copy', 'all', '-optimize', '-progressive', '-outfile', alt, src])
            if alt.stat().st_size < dst.stat().st_size: os.replace(alt, dst)
        finally:
            alt.unlink(missing_ok=True)
    elif fmt == 'PNG':
        command([PNG, '-o', '3', '--out', dst, src])
    else: return False
    validate(src, dst)
    return True

class Engine:
    def __init__(self, root, year=None, log=print, stop=None, state=None, cloud=True,
                 profile='lossless', event=None, legacy=None):
        self.root = Path(root).resolve()
        self.year = str(year) if year is not None else 'pasta-'+hashlib.sha256(str(self.root).encode()).hexdigest()[:12]
        self.folder = self.root / ('ANO ' + str(year)) if year is not None else self.root
        self.profile, self.event, self.total = profile, event, 0
        if profile != 'lossless' and profile not in PROFILES: raise ValueError('Perfil desconhecido')
        self.state = Path(state or BASE / (PROFILES[profile]['folder'] if profile in PROFILES else 'dados')).resolve()
        self.legacy = Path(legacy or BASE/'dados/progresso.sqlite')
        self.state.mkdir(parents=True, exist_ok=True)
        self.log, self.stop, self.cloud = log, stop or threading.Event(), cloud
        self.db = sqlite3.connect(self.state / 'progresso.sqlite')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, data TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS history (path TEXT, hash TEXT, data TEXT NOT NULL, PRIMARY KEY(path,hash))')

    def save(self, p, d):
        d['updated'] = time.strftime('%Y-%m-%d %H:%M:%S')
        old=self.db.execute('SELECT data FROM files WHERE path=?',(str(p),)).fetchone()
        if old:
            prior=json.loads(old[0])
            if prior.get('new_hash') and prior.get('backup'):
                self.db.execute('INSERT OR REPLACE INTO history VALUES (?,?,?)',
                                (str(p),prior['new_hash'],old[0]))
        self.db.execute('INSERT OR REPLACE INTO files VALUES (?,?)', (str(p), json.dumps(d)))
        self.db.commit()
        self.report()

    def rows(self):
        return [(Path(p), json.loads(d)) for p, d in self.db.execute('SELECT path,data FROM files')
                if Path(p).is_relative_to(self.folder)]

    def report(self):
        rows = self.rows()
        saved = sum(d.get('before', 0)-d.get('after', d.get('before', 0))
                    for _, d in rows if d.get('status') in ('pending', 'synced', 'local'))
        before = sum(d.get('before', 0) for _, d in rows
                     if d.get('status') in ('pending','synced','local','unchanged'))
        counts = {}
        for _, d in rows: counts[d['status']] = counts.get(d['status'], 0) + 1
        if self.event:
            self.event(dict(kind='metrics',total=self.total,**summarize(rows)))
        out = self.state / ('relatorio-' + self.year)
        tmp = out.with_suffix('.csv.tmp')
        with open(tmp, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f, delimiter=';')
            writer.writerow(['arquivo', 'estado', 'bytes_antes', 'bytes_depois', 'economia_bytes', 'erro', 'backup'])
            for p, d in rows:
                a, b = d.get('before', 0), d.get('after', d.get('before', 0))
                writer.writerow([str(p), d['status'], a, b, a-b if d['status'] in ('pending','synced','local') else 0,
                                 d.get('error', ''), d.get('backup', '')])
        os.replace(tmp, out.with_suffix('.csv'))
        text = (f'Pasta {self.folder} — perfil {self.profile}\nArquivos registrados: {len(rows)}\nEstados: {counts}\n'
                f'Economia local confirmada: {saved:,} bytes ({saved/1024**2:.2f} MiB)\n'
                f'Reducao sobre arquivos processados com sucesso: {100*saved/before if before else 0:.2f}%\n'
                f'Tamanho antes: {before:,} bytes; depois: {before-saved:,} bytes (arquivos processados neste perfil).\n'
                'synced = sincronizado no OneDrive; local = salvo localmente; pending = envio nao confirmado.\n'
                'Backups locais sao mantidos e ocupam espaco; economia acima refere-se aos arquivos de origem.\n')
        tmp = out.with_suffix('.txt.tmp'); tmp.write_text(text, encoding='utf-8')
        os.replace(tmp, out.with_suffix('.txt'))
        return text

    def check_pending(self, p, d):
        current = sha(p)
        if current == d.get('new_hash'):
            if d['status'] == 'prepared':
                d['status'] = 'pending'; self.save(p, d)
            if not self.cloud or sync_state(p) is True:
                d['status'] = 'synced' if self.cloud else 'local'; self.save(p, d)
            return True
        if current != d.get('old_hash'):
            d.update(status='conflict', error='Arquivo alterado externamente; nenhuma substituicao realizada.')
            self.save(p, d)
            return True
        return False

    def process(self, p):
        p = Path(p).resolve()
        if not p.is_relative_to(self.folder): raise ValueError('Arquivo fora da pasta selecionada')
        record = self.db.execute('SELECT data FROM files WHERE path=?', (str(p),)).fetchone()
        d = json.loads(record[0]) if record else {}
        if d.get('status') == 'conflict': return
        if d.get('status') == 'local' and self.cloud:
            d['status']='pending'; self.save(p,d)
            if self.check_pending(p,d): return
        if d.get('status') in ('synced', 'local', 'unchanged', 'unsupported'):
            st = p.stat()
            if [st.st_size, st.st_mtime_ns] == d.get('signature'): return
            if d.get('new_hash') and sha(p) == d['new_hash']: return
            d = {}  # Nova versao externa: novo backup e nova tentativa.
        if d.get('status') in ('prepared', 'pending'):
            if self.check_pending(p, d): return
            # Queda antes da troca: original ainda intacto; candidato sera recalculado.
        self.log(f'Baixando/verificando: {p.relative_to(self.folder)}')
        key = hashlib.sha256(str(p).encode()).hexdigest()
        job = self.state / 'temporarios' / key
        job.mkdir(parents=True, exist_ok=True)
        source, result = job / 'original', job / 'reduzido'
        # A leitura integral hidrata arquivos somente online. Subprocesso permite timeout.
        if shutil.disk_usage(self.state).free < p.stat().st_size * 4 + 256*1024**2:
            raise RuntimeError('Espaco local insuficiente para backup e verificacao')
        initial = p.stat()
        command([sys.executable, Path(__file__), '--copy', p, source], timeout=300)
        final = p.stat()
        if (initial.st_size, initial.st_mtime_ns) != (final.st_size, final.st_mtime_ns):
            raise RuntimeError('Arquivo mudou durante o download; sera tentado novamente')
        old_hash = sha(source)
        optimize_source = source
        original_backup = None
        if self.profile in PROFILES:
            saved_original=original_for(p,old_hash,databases(BASE)+[self.legacy,self.state/'progresso.sqlite'])
            if saved_original:
                optimize_source, original_backup=saved_original,str(saved_original)
        d = dict(status='processing', before=source.stat().st_size, old_hash=old_hash,
                 profile=self.profile, original_backup=original_backup,
                 original_hash=sha(optimize_source) if original_backup else old_hash)
        self.save(p, d)
        if not optimize(optimize_source, result, self.profile):
            d.update(status='unsupported', signature=[final.st_size, final.st_mtime_ns])
            self.save(p, d); return
        if result.stat().st_size >= source.stat().st_size:
            d.update(status='unchanged', new_hash=old_hash, after=source.stat().st_size,
                     signature=[final.st_size, final.st_mtime_ns])
            self.save(p, d)
            source.unlink(missing_ok=True); result.unlink(missing_ok=True)
            return
        backup = self.state / 'backups' / key / (old_hash + p.suffix)
        if not backup.exists() or sha(backup) != old_hash:
            part = backup.with_suffix('.partial')
            durable_copy(source, part)
            if sha(part) != old_hash: raise RuntimeError('Falha na verificacao do backup')
            os.replace(part, backup)
        new_hash = sha(result)
        d.update(status='prepared', after=result.stat().st_size, new_hash=new_hash, backup=str(backup))
        self.save(p, d)  # Journal duravel ANTES da substituicao.
        if sha(p) != old_hash: raise RuntimeError('Original alterado externamente; substituicao cancelada')
        temp = p.with_name('.codex-' + key[:16] + '.tmp')
        try:
            durable_copy(result, temp)
            if sha(temp) != new_hash: raise RuntimeError('Falha na copia final')
            os.utime(temp, ns=(initial.st_atime_ns, initial.st_mtime_ns))
            if sha(p) != old_hash: raise RuntimeError('Original alterado antes da troca')
            os.replace(temp, p)  # Rename atomico, no mesmo diretorio/volume.
        finally:
            temp.unlink(missing_ok=True)
        if sha(p) != new_hash: raise RuntimeError('Verificacao apos substituicao falhou; backup preservado')
        st = p.stat()
        d.update(status='pending', signature=[st.st_size, st.st_mtime_ns])
        self.save(p, d)
        source.unlink(missing_ok=True); result.unlink(missing_ok=True)
        self.log(f'Reduzido: {p.name} — economia {d["before"]-d["after"]:,} bytes')

    def wait_pending(self, seconds=120):
        deadline = time.monotonic() + seconds
        while not self.stop.is_set():
            pending = [(p,d) for p,d in self.rows() if d['status'] in ('prepared','pending')]
            if not pending: return True
            for p,d in pending:
                try: self.check_pending(p,d)
                except OSError as e: self.log(f'Sincronizacao pendente: {p.name}: {e}')
            if not any(d['status'] in ('prepared','pending') for _,d in self.rows()): return True
            if time.monotonic() >= deadline: return False
            self.stop.wait(5)
        return False

    def run(self):
        if not self.folder.is_dir(): raise ValueError('Pasta nao encontrada: ' + str(self.folder))
        if self.state.is_relative_to(self.root): raise ValueError('Backups devem ficar fora da pasta de origem')
        self.log('Enumerando imagens em ' + str(self.folder) + '...')
        files = []
        def walk_error(error): raise error
        for directory, dirs, names in os.walk(self.folder, followlinks=False, onerror=walk_error):
            dirs[:] = [x for x in dirs if not (Path(directory)/x).is_symlink() and not (Path(directory)/x).is_junction()]
            for name in names:
                p = Path(directory)/name
                if name.startswith('.codex-') or p.is_symlink(): continue
                files.append(p)
        self.log(f'{len(files)} arquivos encontrados. JPG/JPEG/PNG e arquivos sem extensao serao examinados.')
        self.total = len(files)
        self.report()
        for index,p in enumerate(sorted(files), 1):
            if self.stop.is_set(): break
            self.log(f'[{index}/{len(files)}] {p.name}')
            if p.suffix.lower() not in ('.jpg','.jpeg','.png',''):
                if not self.db.execute('SELECT 1 FROM files WHERE path=?',(str(p),)).fetchone():
                    self.save(p, dict(status='unsupported', error='Formato preservado sem alteracao'))
                continue
            for attempt in range(3):
                if self.stop.is_set(): break
                try:
                    self.process(p); break
                except Exception as e:
                    self.log(f'Tentativa {attempt+1}/3: {p.name}: {e}')
                    row = self.db.execute('SELECT data FROM files WHERE path=?',(str(p),)).fetchone()
                    d = json.loads(row[0]) if row else {}
                    # Nunca descartar o journal de uma troca potencialmente concluida.
                    if d.get('status') not in ('prepared','pending'): d['status']='error'
                    d['error'] = str(e); self.save(p,d)
                    if attempt < 2: self.stop.wait(5*(attempt+1))
            if sum(d['status']=='pending' for _,d in self.rows()) >= 10:
                self.log('Aguardando OneDrive enviar o lote (ate 10 pendencias)...')
                while not self.stop.is_set() and not self.wait_pending():
                    self.log('OneDrive ainda pendente. Aguardando automaticamente; pode pausar e retomar.')
        self.log('Verificando envios pendentes...')
        if not self.stop.is_set(): self.wait_pending()
        self.log(self.report())
        self.log('Pausado; progresso salvo.' if self.stop.is_set() else 'Varredura encerrada. Consulte erros e pendencias no relatorio.')

def gui():
    from interface import show
    show(Engine, BASE, DEFAULT_ROOT)


def main():
    if len(sys.argv)>1 and sys.argv[1]=='--copy':
        durable_copy(sys.argv[2],sys.argv[3]); return
    (BASE/'dados').mkdir(parents=True,exist_ok=True)
    lock=open(BASE/'dados/execucao.lock','a+b'); lock.seek(0); lock.write(b'0'); lock.flush(); lock.seek(0)
    try: msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    except OSError:
        ctypes.windll.user32.MessageBoxW(0,'O programa ja esta aberto.','Otimizador',0); return
    try:
        if not JPEG.is_file() or not PNG.is_file():
            ctypes.windll.user32.MessageBoxW(0,'Execute python setup_tools.py para preparar jpegtran e Oxipng.','Preparação necessária',0)
            return
        gui()
    finally: lock.close()

if __name__ == '__main__': main()

"""Perfis versionados e leitura dos backups compartilhados entre perfis."""
from pathlib import Path
from contextlib import closing
import sqlite3, json, hashlib

PROFILES = {
    'leve-v1': dict(label='Leve', folder='dados-leve', edge=None, quality=93, psnr=32,
                    description='JPEG: resolução original • qualidade 93 • PNG sem perda'),
    'moderado-v1': dict(label='Moderado', folder='dados-moderado', edge=2800, quality=85, psnr=30,
                    description='JPEG: até 2800 px • qualidade 85 • PNG sem perda'),
    'forte-v1': dict(label='Forte', folder='dados-forte', edge=2000, quality=75, psnr=28,
                    description='JPEG: até 2000 px • qualidade 75 • PNG sem perda'),
}

def digest(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest()

def databases(base):
    return [base/name/'progresso.sqlite' for name in ['dados']+[p['folder'] for p in PROFILES.values()]]

def records(dbs, path=None):
    result=[]
    for db in dict.fromkeys(Path(x).resolve() for x in dbs):
        if not db.is_file(): continue
        with closing(sqlite3.connect(db.as_uri()+'?mode=ro',uri=True,timeout=10)) as conn:
            tables={x[0] for x in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in ('files','history'):
                if table not in tables: continue
                query=f'SELECT path,data FROM {table}'
                rows=conn.execute(query+' WHERE path=?',(str(path),)) if path else conn.execute(query)
                result.extend((Path(p),json.loads(d)) for p,d in rows)
    return result

def original_for(path, current_hash, dbs):
    """Segue apenas versões ligadas ao hash atual; valida cada backup utilizado."""
    items=[d for _,d in records(dbs,path)]
    seen=set(); selected=None; cursor=current_hash
    while cursor not in seen:
        seen.add(cursor)
        linked=[d for d in items if d.get('new_hash')==cursor and d.get('backup')]
        if not linked: break
        d=next((d for d in linked if d.get('original_backup')),linked[0])
        candidate=Path(d.get('original_backup') or d['backup'])
        expected=d.get('original_hash') if d.get('original_backup') else d.get('old_hash')
        expected=expected or candidate.stem
        if not candidate.is_file() or digest(candidate)!=expected:
            raise ValueError('Backup original ausente ou inválido: '+str(candidate))
        selected=candidate;cursor=expected
    return selected

def summarize(rows):
    counts={}
    for _,d in rows: counts[d['status']]=counts.get(d['status'],0)+1
    successful=[d for _,d in rows if d['status'] in ('pending','synced','unchanged')]
    before=sum(d.get('before',0) for d in successful)
    after=sum(d.get('after',d.get('before',0)) for d in successful)
    confirmed=sum(d.get('before',0)-d.get('after',d.get('before',0)) for d in successful if d['status']=='synced')
    return dict(counts=counts,before=before,after=after,saved=before-after,
                confirmed=confirmed,percent=100*(before-after)/before if before else 0)

def backup_sizes(base):
    # Todos os anos/perfis. Mede arquivos, não alocação física NTFS ou placeholders OneDrive.
    size=0
    for db in databases(base):
        folder=db.parent/'backups'
        if folder.exists():
            for p in folder.rglob('*'):
                if p.is_file(): size+=p.stat().st_size
    return size

def human(n):
    return f'{n/1e9:.2f} GB' if abs(n)>=1e9 else f'{n/1e6:.1f} MB'

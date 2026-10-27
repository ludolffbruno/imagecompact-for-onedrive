import tempfile, unittest, sys
from pathlib import Path
from unittest.mock import patch
from PIL import Image
import otimizador as o

class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.base=Path(self.tmp.name).resolve()
        self.folder=self.base/'origem/ANO 2023/Empresa Exemplo/subpasta'
        self.folder.mkdir(parents=True)
        self.p=self.folder/'foto.jpg'
        im=Image.new('RGB',(128,128),'white')
        exif=Image.Exif(); exif[274]=6; exif[270]='Metadados preservados'
        im.save(self.p,quality=95,optimize=False,exif=exif)
        self.original=self.p.read_bytes()
        self.e=o.Engine(self.base/'origem',2023,lambda x:None,state=self.base/'estado',cloud=False)
    def tearDown(self):
        self.e.db.close(); self.tmp.cleanup()
    def test_roundtrip_backup_resume_and_pdf(self):
        pdf=self.folder/'nota.pdf'; pdf.write_bytes(b'nao alterar pdf')
        self.e.run()
        d=self.e.rows()[0][1]
        rows=dict(self.e.rows()); d=rows[self.p]
        self.assertEqual(d['status'],'synced')
        self.assertLess(len(self.p.read_bytes()),len(self.original))
        self.assertEqual(Path(d['backup']).read_bytes(),self.original)
        o.validate(Path(d['backup']),self.p)
        before=self.p.read_bytes(); self.e.run()
        self.assertEqual(self.p.read_bytes(),before)
        self.assertEqual(pdf.read_bytes(),b'nao alterar pdf')
    def test_crash_after_atomic_replace(self):
        original_replace=o.os.replace
        def crash(a,b):
            original_replace(a,b)
            if Path(b)==self.p: raise RuntimeError('simulacao de queda apos troca')
        with patch.object(o.os,'replace',crash):
            with self.assertRaises(RuntimeError): self.e.process(self.p)
        self.assertEqual(dict(self.e.rows())[self.p]['status'],'prepared')
        replaced=self.p.read_bytes()
        self.e.process(self.p)
        self.assertEqual(dict(self.e.rows())[self.p]['status'],'synced')
        self.assertEqual(self.p.read_bytes(),replaced)
    def test_corruption_never_replaces_original(self):
        with patch.object(o,'validate',side_effect=ValueError('corrompido')):
            with self.assertRaises(ValueError): self.e.process(self.p)
        self.assertEqual(self.p.read_bytes(),self.original)
    def test_external_change_detected_on_recovery(self):
        self.e.process(self.p)
        self.p.write_bytes(b'alteracao externa')
        self.e.process(self.p)
        self.assertEqual(dict(self.e.rows())[self.p]['status'],'conflict')
        self.assertEqual(self.p.read_bytes(),b'alteracao externa')
    def test_png_pixels_and_metadata(self):
        from PIL.PngImagePlugin import PngInfo
        p=self.folder/'imagem.PNG'
        meta=PngInfo();meta.add_text('descricao','teste')
        Image.new('RGBA',(128,128),(255,0,0,128)).save(p,compress_level=0,pnginfo=meta)
        old=p.read_bytes();self.e.process(p)
        d=dict(self.e.rows())[p]
        self.assertEqual(Path(d['backup']).read_bytes(),old)
        o.validate(Path(d['backup']),p)
        self.assertLess(p.stat().st_size,len(old))
    def test_cloud_status_local_is_unknown(self):
        self.assertIsNone(o.sync_state(self.p))
    def test_crash_before_replace(self):
        original_replace=o.os.replace
        def crash(a,b):
            if Path(b)==self.p: raise RuntimeError('queda antes da troca')
            return original_replace(a,b)
        with patch.object(o.os,'replace',crash):
            with self.assertRaises(RuntimeError): self.e.process(self.p)
        self.assertEqual(self.p.read_bytes(),self.original)
        self.e.process(self.p)
        self.e.wait_pending(seconds=0)
        self.assertEqual(dict(self.e.rows())[self.p]['status'],'synced')
    def test_cloud_unknown_does_not_claim_uploaded(self):
        self.e.cloud=True
        self.e.process(self.p)
        with patch.object(o,'sync_state',return_value=None):
            self.assertFalse(self.e.wait_pending(seconds=0))
        self.assertEqual(dict(self.e.rows())[self.p]['status'],'pending')
        with patch.object(o,'sync_state',return_value=True):
            self.assertTrue(self.e.wait_pending(seconds=0))
    def test_moderate_dimensions_metadata_and_resume(self):
        exif=Image.Exif();exif[274]=6;exif[270]='Original'
        Image.new('RGB',(4080,3060),(180,170,150)).save(self.p,quality=98,exif=exif)
        self.e.profile='moderado-v1'
        self.e.process(self.p);self.e.wait_pending(seconds=0)
        with Image.open(self.p) as im:
            self.assertEqual(im.size,(2800,2100));self.assertEqual(im.getexif()[274],6)
        before=self.p.read_bytes();self.e.process(self.p)
        self.assertEqual(before,self.p.read_bytes())
    def test_profile_migration_uses_original_backup(self):
        self.e.process(self.p);self.e.wait_pending(seconds=0)
        legacy=self.e.state/'progresso.sqlite'
        e=o.Engine(self.base/'origem',2023,lambda x:None,state=self.base/'moderado',
                   cloud=False,profile='moderado-v1',legacy=legacy)
        original_optimize=o.optimize
        observed=[]
        def record(src,dst,profile):
            observed.append(Path(src).read_bytes())
            return original_optimize(src,dst,profile)
        try:
            with patch.object(o,'optimize',record): e.process(self.p)
            self.assertEqual(observed,[self.original])
        finally: e.db.close()
    def test_all_profile_dimensions_and_metadata(self):
        from perfis import PROFILES
        exif=Image.Exif();exif[274]=6
        Image.new('RGB',(4080,3060),(130,150,190)).save(self.p,quality=98,exif=exif)
        for profile,edge in [('leve-v1',4080),('moderado-v1',2800),('forte-v1',2000)]:
            target=self.base/(profile+'.jpg')
            o.optimize(self.p,target,profile)
            with Image.open(target) as im:
                self.assertEqual(im.width,edge);self.assertEqual(im.getexif()[274],6)
            self.assertEqual(o.jpeg_metadata(self.p),o.jpeg_metadata(target))
    def test_metrics_include_unchanged_and_pending(self):
        from perfis import summarize
        rows=[(self.p,dict(status='synced',before=100,after=40)),
              (self.p,dict(status='pending',before=200,after=80)),
              (self.p,dict(status='unchanged',before=20,after=20)),
              (self.p,dict(status='error',before=50))]
        m=summarize(rows)
        self.assertEqual((m['before'],m['after'],m['saved'],m['confirmed']),(320,140,180,60))
    def test_original_chain_and_backup_accounting(self):
        from perfis import original_for,backup_sizes
        exif=Image.Exif();exif[274]=1
        Image.new('RGB',(4080,3060),(50,80,90)).save(self.p,quality=98,exif=exif)
        original=self.p.read_bytes()
        mod=o.Engine(self.base/'origem',2023,lambda x:None,state=self.base/'dados-moderado',
                     cloud=False,profile='moderado-v1')
        strong=None
        try:
            mod.process(self.p);mod.wait_pending(0)
            strong=o.Engine(self.base/'origem',2023,lambda x:None,state=self.base/'dados-forte',
                cloud=False,profile='forte-v1',legacy=mod.state/'progresso.sqlite')
            strong.process(self.p);strong.wait_pending(0)
            found=original_for(self.p,o.sha(self.p),[mod.state/'progresso.sqlite',strong.state/'progresso.sqlite'])
            self.assertEqual(found.read_bytes(),original)
            self.assertGreater(backup_sizes(self.base),len(original))
            reduced=self.p.read_bytes();strong.process(self.p)
            self.assertEqual(reduced,self.p.read_bytes())
            found.write_bytes(b'corrompido')
            with self.assertRaises(ValueError):
                original_for(self.p,o.sha(self.p),[strong.state/'progresso.sqlite'])
        finally:
            mod.db.close()
            if strong:strong.db.close()
    def test_viewer_preview_does_not_change_input(self):
        from comparador import load_pair,viewport
        before=self.p.read_bytes()
        images,sizes,has_original=load_pair(self.p,self.base,'leve-v1',preview=True)
        self.assertFalse(has_original)
        self.assertEqual(self.p.read_bytes(),before)
        rendered=viewport(images[0],images[0].size,(.5,.5),1,(200,100))
        self.assertEqual(rendered.size,(200,100))
    def test_unknown_profile_rejected(self):
        with self.assertRaises(ValueError):o.optimize(self.p,self.base/'out','invalido')

if __name__=='__main__': unittest.main(verbosity=2)

"""Comparação sincronizada por região, com renderização apenas da área visível."""
import tkinter as tk
from tkinter import ttk, filedialog
from pathlib import Path
import threading, queue, tempfile, shutil
from PIL import Image, ImageOps, ImageTk
from perfis import PROFILES, databases, original_for, digest, human

def load_pair(path, base, profile, preview=False):
    from optimizer import optimize
    path=Path(path).resolve()
    # Cópia temporária mantém a leitura consistente enquanto o OneDrive trabalha.
    with tempfile.TemporaryDirectory(prefix='imagecompact-preview-') as folder:
        current=Path(folder)/'atual'
        shutil.copyfile(path,current)
        original=original_for(path,digest(current),databases(base))
        source=original or current
        target=current
        if preview:
            target=Path(folder)/'previa'
            if not optimize(source,target,profile): raise ValueError('Formato não compatível com prévia')
        pictures=[]
        for p in (source,target):
            with Image.open(p) as im:
                im.load();pictures.append(ImageOps.exif_transpose(im).convert('RGB'))
        return pictures, [source.stat().st_size,target.stat().st_size], bool(original)

def viewport(im, reference_size, center, scale, size):
    """Mesma região proporcional nas duas imagens, sem ampliar a imagem inteira."""
    width,height=size;rw,rh=reference_size
    cx,cy=center
    halfx=width/(2*scale*rw);halfy=height/(2*scale*rh)
    box=((cx-halfx)*im.width,(cy-halfy)*im.height,(cx+halfx)*im.width,(cy+halfy)*im.height)
    return im.transform((width,height),Image.Transform.EXTENT,box,
                        resample=Image.Resampling.BICUBIC,fillcolor=(15,23,36))

def open_comparator(parent, base, root, year, profile):
    win=tk.Toplevel(parent);win.title('Comparar imagens • original e resultado')
    win.geometry('1180x780');win.minsize(800,540);win.configure(bg='#0b1220')
    selected=tk.StringVar();mode=tk.StringVar(value='atual');info=tk.StringVar(value='Escolha uma imagem para comparar.')
    toolbar=tk.Frame(win,bg='#141f32',padx=12,pady=12);toolbar.pack(fill='x')
    canvases=[];headings=[];photos=[];images=[];center=[.5,.5];scale=.25;generation=0;closing=False
    events=queue.Queue()
    def choose():
        path=filedialog.askopenfilename(parent=win,initialdir=str(Path(root)/('ANO '+str(year)) if year is not None else Path(root)),
            title='Selecionar imagem',filetypes=[('Imagens','*.jpg *.jpeg *.png'),('Todos','*.*')])
        if path: selected.set(path);load()
    def load():
        nonlocal generation
        if not selected.get(): return
        generation+=1;token=generation
        path=selected.get();preview=mode.get()=='previa'
        info.set('Carregando imagem e verificando backup…')
        def work():
            try: events.put((token,load_pair(path,base,profile,preview),None))
            except Exception as e: events.put((token,None,str(e)))
        threading.Thread(target=work,daemon=True).start()
    def draw():
        if not images or closing: return
        photos.clear()
        for im,canvas in zip(images,canvases):
            size=(max(1,canvas.winfo_width()),max(1,canvas.winfo_height()))
            photo=ImageTk.PhotoImage(viewport(im,images[0].size,center,scale,size),master=win)
            photos.append(photo);canvas.delete('all');canvas.create_image(0,0,anchor='nw',image=photo)
        zoomtext.set(f'{scale*100:.0f}% do original • arraste para mover as duas imagens')
    def fit():
        nonlocal scale
        if images:
            center[:]=[.5,.5]
            scale=min(canvases[0].winfo_width()/images[0].width,canvases[0].winfo_height()/images[0].height)*.96
            draw()
    def zoom(factor):
        nonlocal scale
        scale=max(.02,min(4,scale*factor));draw()
    def full():
        nonlocal scale
        scale=1;draw()
    def button(text,command):
        tk.Button(toolbar,text=text,command=command,bg='#25334a',fg='#edf3fc',relief='flat',
                  padx=10,pady=6).pack(side='left',padx=3)
    button('Escolher imagem',choose)
    choice=ttk.Combobox(toolbar,state='readonly',width=26,values=['Arquivo atual','Prévia: '+PROFILES[profile]['label']])
    choice.current(0);choice.pack(side='left',padx=10)
    choice.bind('<<ComboboxSelected>>',lambda e:(mode.set('atual' if choice.current()==0 else 'previa'),load()))
    button('−',lambda:zoom(1/1.25));button('+',lambda:zoom(1.25));button('100%',full);button('Ajustar',fit)
    zoomtext=tk.StringVar(value='Zoom sincronizado')
    tk.Label(win,textvariable=selected,bg='#0b1220',fg='#97abc6',anchor='w').pack(fill='x',padx=14,pady=6)
    body=tk.Frame(win,bg='#0b1220');body.pack(fill='both',expand=True,padx=12)
    drag=[0,0]
    def begin(event): drag[:]=[event.x,event.y]
    def move(event):
        if not images:return
        center[0]=max(0,min(1,center[0]-(event.x-drag[0])/(images[0].width*scale)))
        center[1]=max(0,min(1,center[1]-(event.y-drag[1])/(images[0].height*scale)))
        drag[:]=[event.x,event.y];draw()
    for i in range(2):
        frame=tk.Frame(body,bg='#141f32');frame.pack(side='left',fill='both',expand=True,padx=3)
        heading=tk.StringVar(value='Original' if i==0 else 'Resultado');headings.append(heading)
        tk.Label(frame,textvariable=heading,bg='#141f32',fg='#edf3fc',pady=10).pack(fill='x')
        canvas=tk.Canvas(frame,bg='#0f1724',highlightthickness=0,cursor='fleur');canvas.pack(fill='both',expand=True)
        canvas.bind('<ButtonPress-1>',begin);canvas.bind('<B1-Motion>',move)
        canvas.bind('<MouseWheel>',lambda e:zoom(1.2 if e.delta>0 else 1/1.2))
        canvas.bind('<Configure>',lambda e:draw());canvases.append(canvas)
    tk.Label(win,textvariable=zoomtext,bg='#0b1220',fg='#4de0bc',pady=8).pack()
    tk.Label(win,textvariable=info,bg='#0b1220',fg='#97abc6',wraplength=1100,pady=6).pack(fill='x')
    def poll():
        nonlocal images
        if closing:return
        try:
            while True:
                token,result,error=events.get_nowait()
                if token!=generation: continue
                if error:
                    info.set('Não foi possível comparar: '+error);continue
                images,sizes,has_original=result
                headings[0].set(('Original do backup' if has_original else 'Entrada atual — sem backup anterior')+
                    f' • {images[0].width} × {images[0].height} • {human(sizes[0])}')
                headings[1].set(('Prévia '+PROFILES[profile]['label'] if mode.get()=='previa' else 'Arquivo atual')+
                    f' • {images[1].width} × {images[1].height} • {human(sizes[1])}')
                info.set('Mesma região e escala nas duas imagens. Prévia temporária: nenhum arquivo é substituído. '
                         'Se o resultado for maior, o processamento manterá o arquivo atual.')
                fit()
        except queue.Empty:pass
        win.after(150,poll)
    def close():
        nonlocal closing
        closing=True;win.destroy()
    win.protocol('WM_DELETE_WINDOW',close);poll()
    return win

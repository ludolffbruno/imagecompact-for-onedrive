"""Painel desktop, sem dependencias adicionais alem de Tkinter."""
import tkinter as tk
from tkinter import ttk, filedialog
import threading, queue, os, sys, ctypes, traceback, time, json, sqlite3
from pathlib import Path
from contextlib import closing
from perfis import PROFILES, summarize, backup_sizes, human
import shutil

BG='#0b1220'; CARD='#141f32'; LINE='#25334a'; TEXT='#edf3fc'; MUTED='#97abc6'
ACCENT='#4de0bc'; BLUE='#78aaff'; GOLD='#f6c875'

def show(Engine, base, default_root):
    app=tk.Tk(); app.title('ImageCompact for OneDrive')
    app.geometry(f'1120x{min(920,app.winfo_screenheight()-80)}'); app.minsize(980,800); app.configure(bg=BG)
    app.option_add('*Font',('Segoe UI',10))
    style=ttk.Style(app); style.theme_use('clam')
    style.configure('TCombobox',fieldbackground=LINE,background=LINE,foreground=TEXT,
                    arrowcolor=TEXT,bordercolor=LINE,padding=6)
    style.map('TCombobox',fieldbackground=[('readonly',LINE)],foreground=[('readonly',TEXT)])
    style.configure('Horizontal.TProgressbar',background=ACCENT,troughcolor=LINE,
                    bordercolor=LINE,lightcolor=ACCENT,darkcolor=ACCENT,thickness=9)
    events=queue.Queue(); stop=threading.Event(); worker=None
    rootvar=tk.StringVar(value=default_root); cloud=tk.BooleanVar(value=True)
    profile=tk.StringVar(value='moderado-v1');profilebuttons=[]
    status=tk.StringVar(value='Pronto para iniciar'); current=tk.StringVar(value='Todas as imagens da pasta selecionada e suas subpastas')
    progress_text=tk.StringVar(value='Carregando histórico…'); phase=tk.StringVar(value='AGUARDANDO')
    total=0; state=base/'dados-moderado'; state.mkdir(parents=True,exist_ok=True)
    lastmetrics={};snapshot_id=0;disk_busy=False
    def label(parent,text=None,variable=None,size=10,color=TEXT,bold=False,bg=CARD):
        return tk.Label(parent,text=text,textvariable=variable,font=('Segoe UI',size,'bold' if bold else 'normal'),
                        fg=color,bg=bg,anchor='w')
    def button(parent,text,command,primary=False):
        return tk.Button(parent,text=text,command=command,bg=ACCENT if primary else LINE,
                         fg=BG if primary else TEXT,activebackground='#70efd0' if primary else '#344760',
                         activeforeground=BG if primary else TEXT,relief='flat',bd=0,
                         padx=20,pady=10,cursor='hand2',font=('Segoe UI',10,'bold'),
                         disabledforeground='#687d98')
    content=tk.Frame(app,bg=BG,padx=28,pady=22);content.pack(fill='both',expand=True)
    header=tk.Frame(content,bg=BG);header.pack(fill='x')
    label(header,'IMAGENS  /  OTIMIZAÇÃO EM LOTE',size=9,color=ACCENT,bold=True,bg=BG).pack(anchor='w')
    label(header,'ImageCompact for OneDrive',size=24,bold=True,bg=BG).pack(side='left',pady=(5,2))
    badge=label(header,'  MODERADO  ',size=10,color=ACCENT,bold=True,bg=LINE)
    badge.pack(side='right',pady=10,ipady=8)
    label(content,'Reduza imagens em lote, preserve backups e acompanhe a sincronização.',color=MUTED,bg=BG).pack(anchor='w',pady=(2,20))
    setup=tk.Frame(content,bg=CARD,padx=18,pady=14);setup.pack(fill='x')
    top=tk.Frame(setup,bg=CARD);top.pack(fill='x')
    label(top,'PASTA DE ORIGEM',size=9,color=MUTED,bold=True).pack(side='left')
    row=tk.Frame(setup,bg=CARD);row.pack(fill='x',pady=(6,8))
    def choose_root():
        folder=filedialog.askdirectory(parent=app,title='Escolha qualquer pasta de imagens')
        if folder: rootvar.set(folder);selection_changed()
    folderbutton=button(row,'Escolher pasta',choose_root);folderbutton.pack(side='right',padx=8)
    entry=tk.Entry(row,textvariable=rootvar,bg=BG,fg=TEXT,insertbackground=TEXT,
                   relief='flat',font=('Segoe UI',9));entry.pack(side='left',fill='x',expand=True,ipady=9)
    cloudcheck=tk.Checkbutton(setup,text='Aguardar sincronização do OneDrive (desmarque para pasta local)',variable=cloud,
        command=lambda:selection_changed(),bg=CARD,fg=TEXT,selectcolor=LINE,activebackground=CARD,activeforeground=TEXT)
    cloudcheck.pack(anchor='w',pady=(0,6))
    modes=tk.Frame(setup,bg=CARD);modes.pack(fill='x',pady=(2,8))
    label(modes,'COMPRESSÃO',size=9,color=MUTED,bold=True).pack(side='left',padx=(0,14))
    for key,settings in PROFILES.items():
        b=tk.Radiobutton(modes,text=settings['label'],variable=profile,value=key,indicatoron=False,
            command=lambda:selection_changed(),bg=LINE,fg=TEXT,selectcolor='#216653',
            activebackground='#344760',activeforeground=TEXT,relief='flat',bd=0,
            padx=18,pady=6,cursor='hand2')
        b.pack(side='left',padx=(0,6));profilebuttons.append(b)
    description=tk.StringVar(value=PROFILES[profile.get()]['description'])
    label(setup,variable=description,size=10,color=MUTED).pack(anchor='w')
    label(setup,'Todos os perfis JPEG têm perda. Escolher Leve não restaura arquivos já reduzidos; prévias não alteram arquivos.',size=9,color=GOLD).pack(anchor='w',pady=(4,0))
    cards=tk.Frame(content,bg=BG);cards.pack(fill='x',pady=18)
    values={};titles={};footnotes={}
    for i,(key,title,initial,foot,color) in enumerate([
        ('done','PROCESSADOS','0','neste perfil',TEXT),
        ('saved','ESPAÇO ECONOMIZADO','0 MB','economia adicional nesta etapa',ACCENT),
        ('synced','SINCRONIZADOS','0','confirmados pelo OneDrive',BLUE),
        ('pending','AGUARDANDO ENVIO','0','fila limitada a 10 arquivos',GOLD)]):
        cards.columnconfigure(i,weight=1,uniform='cards')
        card=tk.Frame(cards,bg=CARD,padx=16,pady=14)
        card.grid(row=0,column=i,sticky='nsew',padx=(0 if i==0 else 6,0 if i==3 else 6))
        titles[key]=label(card,title,size=9,color=MUTED,bold=True);titles[key].pack(anchor='w')
        values[key]=tk.StringVar(value=initial)
        label(card,variable=values[key],size=25,color=color,bold=True).pack(anchor='w',pady=6)
        footnotes[key]=label(card,foot,size=8,color=MUTED);footnotes[key].pack(anchor='w')
    sizes=tk.Frame(content,bg=CARD,padx=16,pady=10);sizes.pack(fill='x',pady=(0,12))
    sizesline=tk.StringVar(value='Antes → depois: carregando…')
    diskline=tk.StringVar(value='Backups locais: calculando…')
    label(sizes,variable=sizesline,size=10,bold=True).pack(anchor='w')
    label(sizes,variable=diskline,size=9,color=MUTED).pack(anchor='w',pady=(4,0))
    panel=tk.Frame(content,bg=CARD,padx=18,pady=16);panel.pack(fill='x')
    line=tk.Frame(panel,bg=CARD);line.pack(fill='x')
    label(line,variable=status,size=12,bold=True).pack(side='left')
    label(line,variable=phase,size=9,color=ACCENT,bold=True).pack(side='right')
    progress=ttk.Progressbar(panel,mode='determinate',maximum=100);progress.pack(fill='x',pady=(16,8))
    summary=tk.Frame(panel,bg=CARD);summary.pack(fill='x')
    label(summary,variable=progress_text,size=9,color=MUTED).pack(side='left')
    reduction=tk.StringVar(value='Redução: 0%  •  Erros: 0')
    label(summary,variable=reduction,size=9,color=MUTED).pack(side='right')
    label(panel,variable=current,size=9,color=MUTED).pack(anchor='w',pady=(10,0))
    actions=tk.Frame(content,bg=BG);actions.pack(fill='x',pady=16)
    def emit(message): events.put(message)
    def task(root,chosen,use_cloud):
        eng=None
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
        try:
            eng=Engine(root,None,emit,stop,profile=chosen,event=emit,cloud=use_cloud)
            eng.run()
        except Exception:
            emit(traceback.format_exc())
        finally:
            if eng: eng.db.close()
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
            events.put(None)
    def start():
        nonlocal worker
        if worker and worker.is_alive(): return
        stop.clear(); startbutton.config(state='disabled'); pausebutton.config(state='normal')
        cloudcheck.config(state='disabled');entry.config(state='disabled');folderbutton.config(state='disabled')
        for b in profilebuttons:b.config(state='disabled')
        phase.set('EM EXECUÇÃO');status.set('Processando imagens')
        worker=threading.Thread(target=task,args=(rootvar.get(),profile.get(),cloud.get()),daemon=False);worker.start()
    def pause():
        stop.set();phase.set('PAUSANDO');status.set('Concluindo a operação atual com segurança…')
        pausebutton.config(state='disabled')
    startbutton=button(actions,'▶  Iniciar / Retomar',start,True);startbutton.pack(side='left')
    pausebutton=button(actions,'Ⅱ  Pausar',pause);pausebutton.pack(side='left',padx=10);pausebutton.config(state='disabled')
    button(actions,'Abrir relatórios',lambda:os.startfile(str(state))).pack(side='right')
    def compare():
        from comparador import open_comparator
        open_comparator(app,base,rootvar.get(),None,profile.get())
    button(actions,'Comparar imagens',compare).pack(side='right',padx=8)
    label(content,'ATIVIDADE RECENTE',size=9,color=MUTED,bold=True,bg=BG).pack(anchor='w',pady=(0,8))
    log=tk.Text(content,height=7,bg=CARD,fg=MUTED,relief='flat',padx=14,pady=10,
                font=('Consolas',9),wrap='word',state='disabled',selectbackground=LINE)
    log.pack(fill='both',expand=True)
    label(content,'Retomada por arquivo  •  No modo OneDrive, mantenha a sincronização ativa  •  Suspensão automática inibida',
          size=9,color=MUTED,bg=BG).pack(anchor='w',pady=(12,0))
    def metrics(m):
        nonlocal total,lastmetrics
        lastmetrics=m
        total=m.get('total') or total
        c=m['counts'];done=sum(c.get(x,0) for x in ('synced','local','pending','unchanged','unsupported'))
        error=c.get('error',0)+c.get('conflict',0)
        values['done'].set(f'{done:,}'.replace(',','.'))
        saved=m['saved'];values['saved'].set(f'{saved/1e9:.2f} GB' if saved>=1e9 else f'{saved/1e6:.1f} MB')
        values['synced'].set(str(c.get('synced',0) if cloud.get() else c.get('local',0)+c.get('synced',0)));values['pending'].set(str(c.get('pending',0)))
        progress['value']=100*(done+error)/total if total else 0
        progress_text.set(f'{done+error:,} de {total:,} arquivos verificados'.replace(',','.'))
        reduction.set(f'Redução: {m["percent"]:.1f}%  •  Erros: {error}')
        sizesline.set(f'Antes: {human(m.get("before",0))}  →  Depois: {human(m.get("after",0))}'
                      f'   |   Redução sincronizada: {human(m.get("confirmed",0))}  •  processados na pasta/perfil')
    def load_snapshot():
        nonlocal snapshot_id
        snapshot_id+=1;token=snapshot_id
        folder=Path(rootvar.get()).resolve();db=state/'progresso.sqlite'
        def work():
            try:
                rows=[]
                if db.exists():
                    with closing(sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True)) as conn:
                        rows=[(Path(p),json.loads(d)) for p,d in conn.execute('SELECT path,data FROM files') if Path(p).is_relative_to(folder)]
                count=0
                if folder.exists():
                    for directory,dirs,names in os.walk(folder,followlinks=False):
                        dirs[:]=[d for d in dirs if not (Path(directory)/d).is_symlink() and not (Path(directory)/d).is_junction()]
                        count+=sum(not n.startswith('.codex-') for n in names)
                events.put(dict(kind='snapshot',token=token,total=count,**summarize(rows)))
            except Exception as e:events.put('Falha ao carregar histórico: '+str(e))
        threading.Thread(target=work,daemon=True).start()
    def disk_refresh():
        nonlocal disk_busy
        if not disk_busy:
            disk_busy=True
            def work():
                try:events.put(dict(kind='disk',size=backup_sizes(base),free=shutil.disk_usage(base).free))
                except Exception as e:events.put(dict(kind='disk',error=str(e)))
            threading.Thread(target=work,daemon=True).start()
        app.after(30000,disk_refresh)
    def selection_changed(event=None):
        nonlocal state,total
        state=base/PROFILES[profile.get()]['folder'];state.mkdir(parents=True,exist_ok=True)
        total=0
        badge.config(text='  '+PROFILES[profile.get()]['label'].upper()+'  ')
        description.set(PROFILES[profile.get()]['description'])
        titles['synced'].config(text='SINCRONIZADOS' if cloud.get() else 'SALVOS LOCALMENTE')
        footnotes['synced'].config(text='confirmados pelo OneDrive' if cloud.get() else 'sem confirmação de upload')
        titles['pending'].config(text='AGUARDANDO ENVIO' if cloud.get() else 'AGUARDANDO VERIFICAÇÃO')
        status.set('Pronto para iniciar');phase.set('AGUARDANDO')
        metrics(dict(total=0,**summarize([])));load_snapshot()
    entry.bind('<FocusOut>',lambda e:selection_changed() if not (worker and worker.is_alive()) else None)
    def poll():
        nonlocal disk_busy,total
        try:
            while True:
                msg=events.get_nowait()
                if msg is None:
                    startbutton.config(state='normal');pausebutton.config(state='disabled')
                    cloudcheck.config(state='normal');entry.config(state='normal');folderbutton.config(state='normal')
                    for b in profilebuttons:b.config(state='normal')
                    phase.set('PAUSADO' if stop.is_set() else 'ENCERRADO')
                    status.set('Progresso salvo' if stop.is_set() else 'Varredura encerrada — confira as pendências')
                elif isinstance(msg,dict):
                    if msg['kind']=='disk':
                        disk_busy=False
                        diskline.set('Backups locais: '+(msg['error'] if 'error' in msg else
                            f'{human(msg["size"])} (todas as pastas/perfis)  •  Livre no disco: {human(msg["free"])}')+
                            '  |  Economia no OneDrive não equivale a espaço livre local.')
                    elif msg['kind']=='snapshot':
                        if msg['token']==snapshot_id and not (worker and worker.is_alive()):
                            total=msg['total'];metrics(msg)
                    else:metrics(msg)
                else:
                    msg=str(msg)
                    if msg.startswith('Baixando/'):
                        status.set('Baixando e verificando');current.set(msg.split(': ',1)[-1][-130:])
                    elif msg.startswith('Reduzido:'): status.set('Arquivo reduzido e verificado')
                    elif 'Aguardando OneDrive' in msg or 'OneDrive ainda pendente' in msg:
                        status.set('Aguardando sincronização do OneDrive')
                    log.config(state='normal');log.insert('end',time.strftime('%H:%M:%S')+'  '+msg+'\n');log.see('end')
                    if int(log.index('end-1c').split('.')[0])>500: log.delete('1.0','100.0')
                    log.config(state='disabled')
        except queue.Empty: pass
        app.after(150,poll)
    def close():
        if worker and worker.is_alive():
            pause();app.after(300,close)
        else: app.destroy()
    app.protocol('WM_DELETE_WINDOW',close)
    if '--testar-interface' in sys.argv: app.after(1500,app.destroy)
    load_snapshot();disk_refresh();poll();app.mainloop()

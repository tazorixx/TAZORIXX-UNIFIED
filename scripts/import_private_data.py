"""Atomic one-time TAZORIXX Windows -> Linux private-data migration.

Never sends any message. Refuses to overwrite an existing cloud DB.
"""
from __future__ import annotations
import argparse
import os
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path

p=argparse.ArgumentParser()
p.add_argument('archive',type=Path,help='PRIVATE ZIP made by MIGRATE_LOCAL_DATA.ps1')
p.add_argument('--target',type=Path,default=Path('./data/.tazorixx'))
p.add_argument('--container-root',type=Path,default=Path('/data/.tazorixx'))
a=p.parse_args()
target=a.target.expanduser().resolve()
if target.exists() and any(target.iterdir()):
    raise SystemExit('REFUSED: destination contains data. Move/backup it manually before a one-time import.')
target.parent.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(prefix='.tazorixx-import-',dir=target.parent) as staging:
    stage=Path(staging)
    with zipfile.ZipFile(a.archive) as z:
        members=z.infolist()
        if 'tazorixx.sqlite3' not in z.namelist():raise SystemExit('Missing SQLite database')
        if len(members)>20000 or sum(x.file_size for x in members)>2_000_000_000:
            raise SystemExit('Import is larger than supported safety limits')
        for info in members:
            if info.is_dir():continue
            relative=Path(info.filename.replace('\\','/'))
            if (relative.is_absolute() or '..' in relative.parts or not relative.parts or
                    relative.parts[0] not in ('tazorixx.sqlite3','Website_Exports') or
                    (relative.parts[0]=='tazorixx.sqlite3' and len(relative.parts)!=1)):
                raise SystemExit('Unsafe ZIP path refused')
            dest=(stage/relative).resolve()
            if not dest.is_relative_to(stage.resolve()):raise SystemExit('Unsafe destination path')
            dest.parent.mkdir(parents=True,exist_ok=True)
            with z.open(info) as inp,dest.open('wb') as out:shutil.copyfileobj(inp,out)

    con=sqlite3.connect(stage/'tazorixx.sqlite3')
    con.row_factory=sqlite3.Row
    try:
        if con.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('Source SQLite integrity check failed')
        exports=stage/'Website_Exports'
        names={}
        if exports.is_dir():
            for candidate in exports.rglob('*'):
                if candidate.is_file():names.setdefault(candidate.name,[]).append(candidate)
        updated=0; unresolved=[]
        for table,column in [('generation','output_path'),('whatsapp_demo_jobs','html_path'),('whatsapp_demo_jobs','attachment_path')]:
            try:rows=list(con.execute(f'SELECT rowid AS _row_id, {column} AS old_path FROM {table}'))
            except sqlite3.OperationalError:continue
            for row in rows:
                old=str(row['old_path'] or '').replace('\\','/')
                if not old:continue
                picked=None
                # Prefer the full relative path after Website_Exports (many ZIPs share a name).
                if 'Website_Exports/' in old:
                    tail=old.split('Website_Exports/',1)[1]
                    rel=Path(tail)
                    if not rel.is_absolute() and '..' not in rel.parts:
                        candidate=(exports/rel).resolve()
                        if candidate.is_relative_to(exports.resolve()) and candidate.is_file():picked=candidate
                if picked is None:
                    matches=names.get(old.split('/')[-1],[])
                    if len(matches)==1:picked=matches[0]
                if picked is None:
                    unresolved.append(f'{table}.{column} row {row["_row_id"]}: {old.split("/")[-1]}')
                    continue
                new=a.container_root/picked.relative_to(stage)
                con.execute(f'UPDATE {table} SET {column}=? WHERE rowid=?',(str(new),row['_row_id']))
                updated+=1
        con.commit()
    finally:con.close()
    # Installation is atomic on the same filesystem. No partial DB left after an error.
    if target.exists():target.rmdir()
    os.replace(stage,target)
    print('Imported SQLite and Website_Exports into:',target)
    print('Fixed website file references:',updated)
    print('Unresolved file references:',len(unresolved))
    for line in unresolved[:40]:print('REVIEW:',line)
    print('SENDING IS NOT ENABLED BY MIGRATION. Keep ENABLE_SENDING=false until all jobs are audited.')

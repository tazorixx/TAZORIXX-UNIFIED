"""TAZORIXX independent desktop generator: local import, profiles, HTML exports."""
from __future__ import annotations
import hashlib
import base64
import io
import mimetypes
import os
import tempfile
import html
import json
import re
import sqlite3
import csv
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from openpyxl import load_workbook
from PIL import Image, ImageOps, UnidentifiedImageError

APP_DIR = Path.home() / 'AppData' / 'Local' / 'TAZORIXX' if __import__('sys').platform == 'win32' else Path.home() / '.tazorixx'
DB_NAME = 'tazorixx.sqlite3'
SCHEMA_VERSION = 6
RENDERER_VERSION = '17-process-cards-and-contact-actions-1-icon-contact-promo'  # Invalidate fingerprints after renderer changes without changing SQLite schema
SOURCE_SHEETS = ('Business Details', 'Leads Without Number')
MISSING_WEBSITE = ('', 'website not exist', 'no website', 'none', 'n/a', 'not available', '-')
SOCIAL_HOSTS = ('instagram.com','facebook.com','fb.com','wa.me','whatsapp.com','linkedin.com','youtube.com','x.com','twitter.com','t.me')

# These are generic educational themes, never advertised as verified business services.
DEFAULT_INSPIRATION_TOPICS = {
    'interior designers': [('Space Planning', 'Explore ways to balance space, comfort and everyday functionality.'),
        ('Materials & Finishes', 'Discover how textures, colours and materials influence interior spaces.'),
        ('Lighting & Atmosphere', 'Consider how natural and artificial light shape the feel of a room.')],
    'architects': [('Architectural Ideas', 'Explore how buildings are shaped by purpose, context and form.'),
        ('Planning & Layouts', 'Discover approaches to the use of space, movement and light.'),
        ('Materials & Form', 'Learn how materials and design decisions affect a space.')],
    'furniture': [('Furniture Styles', 'Explore forms, finishes and arrangements for different rooms.'),
        ('Materials & Textures', 'Consider wood, fabric and surface treatments in furniture design.'),
        ('Space & Layout', 'See how the placement of furnishings can change a room.')],
    'plumbers': [('Water Systems', 'Explore the fundamentals of domestic water supply and drainage.'),
        ('Fixtures & Fittings', 'Learn about common types of taps, sinks and plumbing fixtures.'),
        ('Maintenance Awareness', 'Discover general care considerations for household pipework.')],
    'carpenters': [('Wood & Materials', 'Discover how different timbers and finishes are used in design.'),
        ('Woodworking Ideas', 'Explore common approaches to shaping and joining wood.'),
        ('Design Inspiration', 'Consider practical ways wood can enhance indoor spaces.')],
    'general': [('Industry Overview', 'Discover general ideas and information related to this business category.'),
        ('Planning Considerations', 'Explore questions to consider when researching businesses in this field.'),
        ('Getting in Touch', 'Use the verified contact details below to ask the business directly.')]
}

DEFAULT_PROFILES = {
    'interior designers': {
        'category': 'Interior Design',
        'industry_aliases': ['interior designers','interior designer','interior design'],
        'headlines': ['Thoughtful spaces. Timeless design.', 'Spaces designed around living.', 'A fresh perspective on interiors.'],
        'introductions': ['Explore interior design ideas for residential and commercial spaces.', 'Discover design inspiration, materials and thoughtful ways to plan interiors.'],
        'subtypes': {'interior designer':'interior designers', 'interior design studio':'interior designers'}
    },
    'architects': {
        'category': 'Architecture',
        'industry_aliases': ['architects','architect','architecture'],
        'headlines': ['Spaces shaped by thoughtful design.', 'Architecture with purpose and clarity.'],
        'introductions': ['Explore architectural concepts, planning approaches and spatial design ideas.'],
        'subtypes': {'architect':'architects', 'architecture firm':'architects'}
    },
    'furniture': {
        'category': 'Furniture',
        'industry_aliases': ['furniture','furniture stores','furniture store'],
        'headlines': ['Furniture for everyday living.', 'Discover furnishing ideas for every space.'],
        'introductions': ['Explore furniture styles, materials and ideas for homes and workplaces.'],
        'subtypes': {'furniture store':'furniture', 'furniture manufacturer':'furniture', 'furniture maker':'furniture'}
    },
    'plumbers': {
        'category': 'Plumbing',
        'industry_aliases': ['plumbers','plumber','plumbing'],
        'headlines': ['Explore local plumbing solutions.'],
        'introductions': ['Find business contact information and available details for local plumbing professionals.'],
        'subtypes': {'plumber':'plumbers'}
    },
    'carpenters': {
        'category': 'Carpentry',
        'industry_aliases': ['carpenters','carpenter','carpentry'],
        'headlines': ['Discover woodworking and carpentry.'],
        'introductions': ['Explore craftsmanship, materials and woodworking ideas.'],
        'subtypes': {'carpenter':'carpenters'}
    },
    'general': {
        'category': 'Local Business',
        'headlines': ['Welcome. Discover more about this business.'],
        'introductions': ['Find available business information and contact details.'],
        'subtypes': {}
    }
}


def _industry_markdown_path():
    """Prefer an editable file beside the portable EXE, then bundled project content."""
    import sys
    locations = []
    if getattr(sys, 'frozen', False):
        locations.append(Path(sys.executable).resolve().parent / 'industry_content.md')
    locations.append(Path(__file__).resolve().parent / 'industry_content.md')
    return next((item for item in locations if item.is_file()), None)


def load_industry_markdown(path=None):
    """Read controlled headings/fields from the editable Markdown industry library.

    Strictly local Markdown; no remote data, HTML execution or Markdown rendering.
    """
    path = Path(path) if path else _industry_markdown_path()
    if not path or not path.is_file():
        return {}
    result = {}
    current = None
    subsection = None
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if line.startswith('## ') and not line.startswith('### '):
            heading = line[3:].strip()
            key = re.sub(r'[^a-z0-9]+', ' ', heading.casefold()).strip()
            if key:
                current = result.setdefault(key, {'category': heading, 'aliases': [],
                    'headlines': [], 'introductions': [], 'services': [], 'guide': [], 'process': []})
            else:
                current = None
            subsection = None
        elif current is None:
            continue
        elif line.startswith('### '):
            subtitle = line[4:].casefold()
            subsection = {'common services':'services', 'planning guide':'guide', 'typical process':'process'}.get(subtitle)
        elif subsection and line.startswith('- ') and '|' in line:
            title, detail = (v.strip() for v in line[2:].split('|', 1))
            if 3 <= len(title) <= 75 and 15 <= len(detail) <= 250:
                current[subsection].append((title, detail))
        elif subsection is None and ': ' in line:
            field, text = line.split(': ', 1)
            if field == 'Aliases':
                current['aliases'] = [x.strip() for x in text.split(',') if x.strip()]
            elif field == 'Category':
                current['category'] = text[:75]
            elif field == 'Headline':
                current['headlines'] = [text[:150]]
            elif field == 'Introduction':
                current['introductions'] = [text[:240]]
    return result


# The Markdown library is authoritative for editable, non-business-specific
# industry ideas. Saved business descriptions and services still take priority.
INDUSTRY_LIBRARY = load_industry_markdown()
for _slug, _entry in INDUSTRY_LIBRARY.items():
    _existing = DEFAULT_PROFILES.setdefault(_slug, {'subtypes': {}})
    _existing['category'] = _entry['category']
    _existing['industry_aliases'] = list(dict.fromkeys(
        _entry['aliases'] + _existing.get('industry_aliases', [])))
    _existing['headlines'] = _entry['headlines'] or _existing.get('headlines', [])
    _existing['introductions'] = _entry['introductions'] or _existing.get('introductions', [])
    if _entry['services']:
        DEFAULT_INSPIRATION_TOPICS[_slug] = _entry['services']


def markdown_topics(slug, name='services'):
    """Re-read the file for live edits without changing the SQLite database."""
    return load_industry_markdown().get(slug, {}).get(name, [])

def val(value):
    return '' if value is None else str(value).strip()

def url_ok(value):
    value = val(value)
    try:
        u = urlparse(value)
        return u.scheme.lower() in ('http','https') and bool(u.netloc) and not re.search(r'\s', u.netloc)
    except ValueError:
        return False

def domain(url):
    try: return (urlparse(url).hostname or '').lower().removeprefix('www.')
    except ValueError: return ''

def website_status(raw):
    v = val(raw).lower().strip()
    if v in MISSING_WEBSITE: return 'missing'
    if not url_ok(v): return 'review'
    host = domain(v)
    if any(host == s or host.endswith('.'+s) for s in SOCIAL_HOSTS): return 'social_only'
    return 'website_present'

def normalize_phone(raw):
    raw = val(raw)
    digits = re.sub(r'\D','',raw)
    if not (10 <= len(digits) <= 15): return ''
    # Existing '+91' phone formatting preserved, default India dial code for 10 digits.
    if digits.startswith('0') and len(digits) == 11: digits = digits[1:]
    if len(digits) == 10: return '+91' + digits
    if digits.startswith('00'): return '+' + digits[2:]
    return '+' + digits

def normalized_email(raw):
    for part in re.split(r'[;,\s]+',val(raw)):
        if re.fullmatch(r'[^@\s]+@[^@\s]+\.[A-Za-z]{2,}',part): return part
    return ''

def maps_key(url):
    url=val(url)
    for pattern in [r'!1s(0x[0-9a-fA-F]+:0x[0-9a-fA-F]+)',r'[?&]cid=(\d+)',r'!16s([^!/?&#]+)']:
        match=re.search(pattern,url)
        if match: return match.group(1)
    # Maintain robust stable source identity when URLs have no canonical place id.
    return 'url:' + hashlib.sha256(url.encode('utf8')).hexdigest()[:28] if url else ''

def stable_key(record):
    m = maps_key(record.get('Google Maps Link'))
    if m: return 'maps:' + m
    name = val(record.get('Business Name')).casefold()
    addr = val(record.get('Address')).casefold()
    return 'fallback:'+hashlib.sha256((name+'|'+addr).encode('utf8')).hexdigest()[:28]

def now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')

def db_connect(path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    conn=sqlite3.connect(path)
    conn.row_factory=sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    # Never migrate a corrupt or unsupported database. Take a consistent snapshot
    # before recording a newer application schema version.
    integrity = conn.execute('PRAGMA integrity_check').fetchone()[0]
    if integrity != 'ok':
        conn.close()
        raise RuntimeError('TAZORIXX database integrity check failed: '+str(integrity))
    version = conn.execute('PRAGMA user_version').fetchone()[0]
    if version > SCHEMA_VERSION:
        conn.close()
        raise RuntimeError('Database belongs to a newer TAZORIXX release. Use that release or restore a verified backup.')
    has_tables = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='businesses'").fetchone()
    if has_tables and version < SCHEMA_VERSION:
        try:
            backup_database(conn, path.parent/'Backups')
        except Exception:
            conn.close()
            raise
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS businesses (
      id TEXT PRIMARY KEY, name TEXT NOT NULL, industry TEXT NOT NULL DEFAULT '', subtype TEXT NOT NULL DEFAULT '',
      phone TEXT NOT NULL DEFAULT '', whatsapp TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT '',
      address TEXT NOT NULL DEFAULT '', maps_url TEXT NOT NULL DEFAULT '', website_raw TEXT NOT NULL DEFAULT '',
      website_status TEXT NOT NULL DEFAULT 'review', rating TEXT NOT NULL DEFAULT '',
      description TEXT NOT NULL DEFAULT '', tagline TEXT NOT NULL DEFAULT '',
      services TEXT NOT NULL DEFAULT '', photos_raw TEXT NOT NULL DEFAULT '', source_json TEXT NOT NULL,
      imported_at TEXT NOT NULL, source_sheet TEXT NOT NULL, content_override TEXT NOT NULL DEFAULT '{}',
      profile_override TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS ix_business_filter ON businesses(website_status,industry);
    CREATE TABLE IF NOT EXISTS imports (
      id INTEGER PRIMARY KEY AUTOINCREMENT, source_path TEXT NOT NULL, source_hash TEXT NOT NULL,
      imported_at TEXT NOT NULL, inserted INTEGER NOT NULL, updated INTEGER NOT NULL,
      rejected INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS profiles (
      slug TEXT PRIMARY KEY, profile_json TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
      updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS supplementary_fields (
      business_id TEXT NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
      label TEXT NOT NULL, source_value TEXT NOT NULL, source_row INTEGER NOT NULL,
      imported_at TEXT NOT NULL,
      PRIMARY KEY (business_id, label, source_value)
    );
    CREATE INDEX IF NOT EXISTS ix_public_fields_business ON supplementary_fields(business_id);
    CREATE TABLE IF NOT EXISTS profile_revisions (
      slug TEXT NOT NULL, version INTEGER NOT NULL, profile_json TEXT NOT NULL,
      saved_at TEXT NOT NULL, PRIMARY KEY(slug, version)
    );
    CREATE TABLE IF NOT EXISTS validation_reports (
      business_id TEXT PRIMARY KEY REFERENCES businesses(id),
      issue_json TEXT NOT NULL, checked_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS generation_jobs (
      id INTEGER PRIMARY KEY AUTOINCREMENT, state TEXT NOT NULL DEFAULT 'paused',
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL, export_dir TEXT NOT NULL,
      template_path TEXT NOT NULL, total INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS generation_job_items (
      job_id INTEGER NOT NULL REFERENCES generation_jobs(id) ON DELETE CASCADE,
      business_id TEXT NOT NULL REFERENCES businesses(id), state TEXT NOT NULL DEFAULT 'pending',
      attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT '',
      output_path TEXT NOT NULL DEFAULT '', PRIMARY KEY(job_id,business_id)
    );
    CREATE TABLE IF NOT EXISTS approved_media (
      owner_type TEXT NOT NULL CHECK(owner_type IN ('business','profile')),
      owner_id TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('hero','gallery')),
      media_name TEXT NOT NULL, mime TEXT NOT NULL, image_bytes BLOB NOT NULL,
      rights_note TEXT NOT NULL, approved_at TEXT NOT NULL,
      PRIMARY KEY(owner_type,owner_id,role,media_name)
    );
    CREATE INDEX IF NOT EXISTS ix_approved_media_owner ON approved_media(owner_type,owner_id,role);
    CREATE TABLE IF NOT EXISTS client_reviews (
      business_id TEXT PRIMARY KEY REFERENCES businesses(id) ON DELETE CASCADE,
      review_status TEXT NOT NULL DEFAULT 'needs_review'
        CHECK(review_status IN ('needs_review','needs_editing','approved')),
      reviewer_notes TEXT NOT NULL DEFAULT '',
      reviewed_html_hash TEXT NOT NULL DEFAULT '',
      updated_at TEXT NOT NULL
    );

        CREATE TABLE IF NOT EXISTS generation (
      business_id TEXT PRIMARY KEY REFERENCES businesses(id), profile_slug TEXT NOT NULL,
      profile_version INTEGER NOT NULL, source_fingerprint TEXT NOT NULL,
      output_path TEXT NOT NULL, html_hash TEXT NOT NULL, exported_at TEXT NOT NULL
    );
    ''')
    with conn:
        for slug,profile in DEFAULT_PROFILES.items():
            conn.execute('INSERT OR IGNORE INTO profiles(slug,profile_json,updated_at) VALUES(?,?,?)',
                         (slug,json.dumps(profile,ensure_ascii=False),now()))
    with conn:
        for r in conn.execute('SELECT slug,version,profile_json FROM profiles'):
            conn.execute('INSERT OR IGNORE INTO profile_revisions VALUES (?,?,?,?)', (r['slug'],r['version'],r['profile_json'],now()))
    # Schema upgrades are additive; the currently supported table layout is
    # unchanged from MVP 04. Version records the successful compatibility check.
    with conn:
        conn.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
    return conn

def record_to_business(source,sheet):
    w=val(source.get('WhatsApp'))
    whnum=normalize_phone(w)
    if not whnum and url_ok(w):
        candidate=re.search(r'(?:wa\.me/|phone=)(\+?\d{10,15})',w)
        if candidate: whnum=normalize_phone(candidate.group(1))
    return {
        'id':stable_key(source), 'name':val(source.get('Business Name')),
        'industry':val(source.get('Selected Business Type')) or val(source.get('Business Type')),
        'subtype':val(source.get('Business Type')),
        'phone':normalize_phone(source.get('Contact Number')),
        'whatsapp':whnum, 'email':normalized_email(source.get('Public Email IDs')),
        'address':val(source.get('Address')), 'maps_url':val(source.get('Google Maps Link')),
        'website_raw':val(source.get('Website')), 'website_status':website_status(source.get('Website')),
        'rating':val(source.get('Google Rating')),'description':val(source.get('Business Description')),
        'tagline':val(source.get('Business Tagline')),'services':val(source.get('Services / Features')),
        'photos_raw':val(source.get('Business Photo URLs')),
        'source_json':json.dumps(source,ensure_ascii=False,default=str),'imported_at':now(),'source_sheet':sheet
    }

CANONICAL_HEADERS = (
    'Business Name','Contact Number','Business Type','Selected Business Type','Website',
    'Public Email IDs','WhatsApp','Address','Google Maps Link','Google Rating',
    'Business Description','Business Tagline','Services / Features','Business Photo URLs'
)
HEADER_ALIASES = {
    'Business Name': ('business name','name','company name','shop name'),
    'Contact Number': ('contact number','phone','phone number','mobile'),
    'Business Type': ('business type','category','subcategory'),
    'Selected Business Type': ('selected business type','industry','selected category'),
    'Website': ('website','website url','site'),
    'Public Email IDs': ('public email ids','email','email address','emails'),
    'WhatsApp': ('whatsapp','whatsapp number','whatsapp url'),
    'Address': ('address','business address','location address'),
    'Google Maps Link': ('google maps link','maps url','google maps url','maps link'),
    'Google Rating': ('google rating','rating'),
    'Business Description': ('business description','description','about'),
    'Business Tagline': ('business tagline','tagline'),
    'Services / Features': ('services / features','services','products'),
    'Business Photo URLs': ('business photo urls','photo urls','images')
}

def inspect_workbook(path):
    """Return sheet headers and suggested mappings without changing the workbook."""
    wb=load_workbook(path,read_only=True,data_only=True)
    try:
        result={}
        for sheet in SOURCE_SHEETS:
            if sheet not in wb.sheetnames: continue
            rows=wb[sheet].iter_rows(values_only=True)
            headers=[val(x) for x in next(rows,())]
            mapping={}
            for target,aliases in HEADER_ALIASES.items():
                matches=[h for h in headers if h and h.casefold() in aliases]
                if matches:mapping[target]=matches[0]
            result[sheet]={'headers':headers,'mapping':mapping}
        return result
    finally:wb.close()

def map_record(record, mapping):
    """Map source headers to standard fields; retain all original source values."""
    result=dict(record)
    for target, source in mapping.items():
        if source in record:result[target]=record[source]
    return result

def import_workbook(conn,path,column_maps=None):
    path=Path(path)
    wb=load_workbook(path,read_only=True,data_only=True)
    count={'inserted':0,'updated':0,'rejected':0,'supplementary':0,'supplementary_linked':0,'supplementary_orphans':0,'supplementary_distinct':0,'sheets':[]}
    source_hasher=hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        with conn:
            for sheet in SOURCE_SHEETS:
                if sheet not in wb.sheetnames: continue
                ws=wb[sheet]; rows=ws.iter_rows(values_only=True)
                try: headers=[val(x) for x in next(rows)]
                except StopIteration: continue
                mapping=(column_maps or {}).get(sheet)
                if mapping is None:
                    mapping={target:next((h for h in headers if h.casefold() in aliases),None)
                             for target,aliases in HEADER_ALIASES.items()}
                    mapping={k:v for k,v in mapping.items() if v}
                if mapping.get('Business Name') not in headers: raise ValueError(f'No mapped Business Name in {sheet}')
                for target,source_header in mapping.items():
                    if source_header not in headers:raise ValueError(f'Mapping {target} points to missing column {source_header}')
                count['sheets'].append(sheet)
                for cells in rows:
                    record=map_record({headers[i]:val(cells[i]) for i in range(min(len(headers),len(cells))) if headers[i]},mapping)
                    if not record.get('Business Name'):count['rejected']+=1;continue
                    b=record_to_business(record,sheet)
                    if not b['id']:count['rejected']+=1;continue
                    exists=conn.execute('SELECT 1 FROM businesses WHERE id=?',(b['id'],)).fetchone()
                    fields=list(b)
                    conn.execute('INSERT INTO businesses ('+','.join(fields)+') VALUES ('+','.join('?' for _ in fields)+') ON CONFLICT(id) DO UPDATE SET '+','.join(f'{k}=excluded.{k}' for k in fields if k!='id'),list(b.values()))
                    count['updated' if exists else 'inserted']+=1
            if 'Other Public Fields' in wb.sheetnames:
                ws=wb['Other Public Fields']
                rows=ws.iter_rows(values_only=True)
                try: headers=[val(v) for v in next(rows)]
                except StopIteration: headers=[]
                required={'Google Maps Link','Field / Label','Public Value'}
                if not required.issubset(headers):
                    raise ValueError('Other Public Fields sheet is missing necessary columns')
                for excel_row, cells in enumerate(rows,start=2):
                    record={headers[i]:val(cells[i]) for i in range(min(len(headers),len(cells))) if headers[i]}
                    if not any(record.values()):continue
                    count['supplementary']+=1
                    listing=maps_key(record.get('Google Maps Link'))
                    bid='maps:'+listing if listing else ''
                    linked=conn.execute('SELECT 1 FROM businesses WHERE id=?',(bid,)).fetchone() if bid else None
                    if not linked:
                        count['supplementary_orphans']+=1
                        continue
                    label=val(record.get('Field / Label'))
                    value=val(record.get('Public Value'))
                    if not label or not value:continue
                    count['supplementary_linked']+=1
                    cursor=conn.execute('INSERT OR IGNORE INTO supplementary_fields '
                       '(business_id,label,source_value,source_row,imported_at) VALUES (?,?,?,?,?)',
                       (bid,label,value,excel_row,now()))
                    count['supplementary_distinct']+=cursor.rowcount

            conn.execute('INSERT INTO imports(source_path,source_hash,imported_at,inserted,updated,rejected) VALUES (?,?,?,?,?,?)',
                        (str(path),source_hasher,now(),count['inserted'],count['updated'],count['rejected']))
    finally: wb.close()
    return count

def list_businesses(conn,search='',industry='',website='missing',limit=5000):
    clauses=['1=1'];args=[]
    if website!='all':clauses.append('website_status=?');args.append(website)
    if industry:clauses.append('lower(industry)=lower(?)');args.append(industry)
    if search:
        clauses.append('(lower(name) LIKE ? OR lower(address) LIKE ? OR lower(subtype) LIKE ?)')
        term='%'+search.casefold()+'%';args.extend([term,term,term])
    args.append(limit)
    return conn.execute('SELECT * FROM businesses WHERE '+' AND '.join(clauses)+' ORDER BY name LIMIT ?',args).fetchall()

def get_profile(conn, business):
    """Assign a profile per business, independent of dashboard filters.

    A specific alias is eligible only when it belongs to the business's
    independently classified industry. Never substitute Interior Design for a
    plumbing/furniture record just because the dashboard shows mixed leads.
    """
    profiles={r['slug']:(json.loads(r['profile_json']),r['version'])
              for r in conn.execute('SELECT * FROM profiles')}
    raw_industry=val(business['industry']).casefold()
    subtype=val(business['subtype']).casefold()
    preferred=val(business['profile_override']).casefold()
    if preferred in profiles:
        return preferred,*profiles[preferred]

    def same(text):
        return re.sub(r'[^a-z0-9]+',' ',val(text).casefold()).strip()

    normalized_industry=same(raw_industry)
    normalized_subtype=same(subtype)

    # Establish the parent family BEFORE evaluating subcategory aliases.
    family=None
    for slug,(profile,_) in profiles.items():
        if slug=='general': continue
        terms=[slug]+profile.get('industry_aliases',[])+DEFAULT_PROFILES.get(slug,{}).get('industry_aliases',[])
        if normalized_industry in {same(v) for v in terms}:
            family=slug;break
    if family is None:
        # Treat a recognized subtype as category evidence only when the
        # selected broad industry is absent or itself vague.
        for slug,(profile,_) in profiles.items():
            if slug=='general' or val(profile.get('parent_slug')):continue
            terms=profile.get('subtypes',{})
            if normalized_subtype in {same(a) for a in terms} and not raw_industry:
                family=slug;break
    if family is None:
        for slug,(profile,_) in profiles.items():
            if slug=='general' or profile.get('parent_slug'):continue
            if same(slug) in normalized_industry and len(same(slug))>=5:
                family=slug;break

    # Resolve subcategories only within the selected family's ancestry.
    def ancestry_contains(profile_slug, ancestor):
        seen=set(); current=profile_slug
        while current and current not in seen:
            if current==ancestor:return True
            seen.add(current)
            item=profiles.get(current)
            current=val(item[0].get('parent_slug')).casefold() if item else ''
        return False

    if family:
        for slug,(profile,version) in profiles.items():
            if slug==family or not ancestry_contains(slug,family):continue
            aliases=profile.get('matching_aliases',[])
            if normalized_subtype and normalized_subtype in {same(a) for a in aliases}:
                return slug,profile,version
        parent=profiles[family][0]
        for alias,target in parent.get('subtypes',{}).items():
            if normalized_subtype==same(alias) and target in profiles and ancestry_contains(target,family):
                return target,*profiles[target]
        return family,*profiles[family]

    # For unknown industries, prefer the generic presentation, but retain the
    # imported category label in the generated website, not Interior Design.
    return 'general',*profiles['general']

def neutral_profile_content(conn, slug, profile):
    """Resolve neutral topics and an intro via saved profile -> parent -> defaults.

    The industry library offers inspiration only; it never asserts that a
    particular business performs a listed service or has completed projects.
    """
    visited=set()
    current=slug
    while current and current not in visited:
        visited.add(current)
        row=conn.execute('SELECT profile_json FROM profiles WHERE slug=?',(current,)).fetchone()
        data=profile if current==slug else (json.loads(row[0]) if row else {})
        topics=data.get('inspiration_topics')
        if isinstance(topics,list) and topics:
            parsed=[]
            for item in topics[:6]:
                if isinstance(item,dict):title=val(item.get('title'));desc=val(item.get('description'))
                elif isinstance(item,str) and '|' in item:title,desc=[x.strip() for x in item.split('|',1)]
                else:continue
                if 3<=len(title)<=75 and 12<=len(desc)<=240:
                    parsed.append((title,desc))
            if parsed:return parsed
        if current in DEFAULT_INSPIRATION_TOPICS:
            return DEFAULT_INSPIRATION_TOPICS[current]
        current=val(data.get('parent_slug')).casefold()
    return DEFAULT_INSPIRATION_TOPICS['general']


def make_abstract_illustration(soup, index, label):
    """Create a self-contained, non-photographic decorative SVG."""
    svg=soup.new_tag('svg',attrs={'viewBox':'0 0 460 330','role':'img',
         'aria-label':f'Abstract illustrative artwork for {label}', 'xmlns':'http://www.w3.org/2000/svg'})
    bg=soup.new_tag('rect',attrs={'x':'0','y':'0','width':'460','height':'330','fill':'#e9ddd0'})
    svg.append(bg)
    circle=soup.new_tag('circle',attrs={'cx':str(100+index*60),'cy':'150','r':'105',
                                'fill':['#c9b39b','#d9c8b7','#c9d0c5'][index%3]})
    svg.append(circle)
    rect=soup.new_tag('rect',attrs={'x':'155','y':str(72+index*18),'width':'218','height':'165',
                                'rx':'16','fill':'#f6f1e8','stroke':'#aa8e72','stroke-width':'7'})
    svg.append(rect)
    path=soup.new_tag('path',attrs={'d':'M173 207 L238 146 L292 184 L362 107',
                                   'fill':'none','stroke':'#987657','stroke-width':'9',
                                   'stroke-linecap':'round'})
    svg.append(path)
    return svg

def choice(options,key):
    if not options:return ''
    return options[int(hashlib.sha256(key.encode('utf8')).hexdigest()[:8],16)%len(options)]

def clean_tagline(raw):
    raw=val(raw)
    # Avoid using scraped multi-sentence boilerplate, contacts, or unverified marketing claims.
    return raw if raw and len(raw)<=120 and not re.search(r'\b\d{10}\b|\b(best|number 1|#1|award[- ]winning)\b',raw,re.I) else ''

def safe_visible_description(raw):
    raw=val(raw)
    if not 35<=len(raw)<=800 or re.search(r'\b(best|number 1|#1|top rated|most trusted)\b',raw,re.I):return ''
    return raw

def safe_services(raw):
    raw=val(raw)
    if not raw or len(raw)>500 or re.search(r'(google maps|add missing|hours might differ|call phone number|directions)',raw,re.I):return []
    values=[x.strip() for x in re.split(r'[;\n|]+',raw) if 2<len(x.strip())<75]
    return values[:6] if values else []

def add_approved_media(conn, owner_type, owner_id, role, filepath, rights_confirmed=False, rights_note=''):
    """Add a user-selected local image without an extra consent dialog.

    Never import remote images from business spreadsheets automatically. The user
    chooses local files, which are resized and embedded into exported HTML.
    Existing calling conventions are supported for backward compatibility.
    """
    if owner_type not in ('business','profile') or role not in ('hero','gallery'):
        raise ValueError('Invalid media owner or role')
    owner_exists = (conn.execute('SELECT 1 FROM businesses WHERE id=?',(owner_id,)).fetchone() if owner_type=='business'
                    else conn.execute('SELECT 1 FROM profiles WHERE slug=?',(owner_id,)).fetchone())
    if not owner_exists: raise ValueError('Business or industry profile does not exist')
    filepath=Path(filepath)
    if filepath.stat().st_size>12_000_000: raise ValueError('Image exceeds 12 MB input limit')
    try:
        with Image.open(filepath) as image:
            if image.width*image.height>35_000_000:raise ValueError('Image dimensions too large')
            image=ImageOps.exif_transpose(image)
            image.thumbnail((1400,1000),Image.Resampling.LANCZOS)
            if image.mode not in ('RGB','L'):image=image.convert('RGB')
            if image.mode=='L':image=image.convert('RGB')
            output=io.BytesIO();image.save(output,format='WEBP',quality=78,method=6)
            image_data=output.getvalue()
    except (UnidentifiedImageError,OSError,ValueError) as exc:
        raise ValueError('Cannot import image: '+str(exc)) from exc
    if len(image_data)>2_000_000:raise ValueError('Optimized image too large')
    # Use unique names for multiple gallery images; hero slots replace the previous hero.
    name=filepath.name
    with conn:
        if role=='hero':conn.execute('DELETE FROM approved_media WHERE owner_type=? AND owner_id=? AND role=?',(owner_type,owner_id,'hero'))
        conn.execute('INSERT OR REPLACE INTO approved_media VALUES (?,?,?,?,?,?,?,?)',
                     (owner_type,owner_id,role,name,'image/webp',image_data,val(rights_note),now()))
    return {'name':name,'bytes':len(image_data),'role':role}


def remove_approved_media(conn, owner_type, owner_id, role, name):
    with conn:
        return conn.execute('DELETE FROM approved_media WHERE owner_type=? AND owner_id=? AND role=? AND media_name=?',
                            (owner_type,owner_id,role,name)).rowcount


def list_approved_media(conn, owner_type, owner_id):
    return conn.execute('SELECT media_name,role,rights_note,approved_at,length(image_bytes) AS size_bytes '
                        'FROM approved_media WHERE owner_type=? AND owner_id=? ORDER BY role,media_name',
                        (owner_type,owner_id)).fetchall()


def media_for_render(conn, business_id, slug, role):
    result=conn.execute('SELECT * FROM approved_media WHERE owner_type=? AND owner_id=? AND role=? ORDER BY approved_at,media_name',
                        ('business',business_id,role)).fetchall()
    if result:return result,'business'
    # Traverse approved industry hierarchy for reusable illustrative media.
    current=slug
    visited=set()
    while current and current not in visited:
        visited.add(current)
        result=conn.execute('SELECT * FROM approved_media WHERE owner_type=? AND owner_id=? AND role=? ORDER BY approved_at,media_name',
                            ('profile',current,role)).fetchall()
        if result:return result,'industry'
        profile=conn.execute('SELECT profile_json FROM profiles WHERE slug=?',(current,)).fetchone()
        current=val(json.loads(profile['profile_json']).get('parent_slug')) if profile else ''
    return [],''


def image_src(row):
    return 'data:'+row['mime']+';base64,'+base64.b64encode(row['image_bytes']).decode('ascii')


def make_responsive_preview(document, name):
    """Generate a local viewport-switching preview shell with an embedded draft."""
    # Inert escaped srcdoc protects the containing shell from its markup.
    escaped=html.escape(document,quote=True)
    title=html.escape(name)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>TAZORIXX Preview — {title}</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#252525;color:#fff;font:14px system-ui,Arial,sans-serif}}
header{{position:sticky;top:0;z-index:10;display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding:10px 18px;background:#181818}}
header strong{{margin-right:auto}}button{{padding:9px 15px;border:1px solid #777;border-radius:8px;background:#353535;color:#fff;cursor:pointer}}
button[aria-pressed="true"]{{background:#b18a57;color:#17110c;border-color:#b18a57}}main{{padding:24px 12px;display:flex;justify-content:center}}
iframe{{display:block;width:100%;height:calc(100vh - 125px);border:0;background:white;box-shadow:0 10px 38px #0006;transition:width .2s ease}}
small{{color:#bbb}}@media(max-width:600px){{main{{padding:10px}}}}
</style></head><body><header><strong>TAZORIXX — {title}</strong><button data-width="100%" aria-pressed="true">Desktop</button><button data-width="768px" aria-pressed="false">Tablet</button><button data-width="390px" aria-pressed="false">Mobile</button><small>Unpublished local preview</small></header>
<main><iframe id="site" title="Generated business website preview" sandbox="allow-scripts allow-forms allow-popups allow-top-navigation-by-user-activation" srcdoc="{escaped}"></iframe></main>
<script>document.querySelectorAll('[data-width]').forEach(b=>b.addEventListener('click',()=>{{document.querySelector('#site').style.width=b.dataset.width;document.querySelectorAll('[data-width]').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));}}));</script></body></html>"""


def update_override(conn,bid,content,profile_slug=''):
    allowed=('name','phone','whatsapp','email','address','description','tagline','services')
    payload={k:val(content.get(k)) for k in allowed if k in content}
    with conn:
        conn.execute('UPDATE businesses SET content_override=?,profile_override=? WHERE id=?',
                     (json.dumps(payload,ensure_ascii=False),profile_slug,bid))

def update_profile(conn,slug,profile):
    slug=val(slug).casefold()
    if not re.fullmatch('[a-z0-9][a-z0-9 _-]{0,75}',slug): raise ValueError('Invalid industry name')
    if profile.get('parent_slug'):
        parent=val(profile['parent_slug']).casefold()
        if parent==slug:raise ValueError('A profile cannot be its own parent')
        if not conn.execute('SELECT 1 FROM profiles WHERE slug=?',(parent,)).fetchone():
            raise ValueError('Parent profile does not exist: '+parent)
    for field in ('matching_aliases','industry_aliases'):
        if field in profile and (not isinstance(profile[field],list) or not all(isinstance(x,str) for x in profile[field])):
            raise ValueError(field+' must be a list of strings')
    if 'inspiration_topics' in profile:
        topics=profile['inspiration_topics']
        if not isinstance(topics,list) or not all(isinstance(x,str) and '|' in x for x in topics):
            raise ValueError('Inspiration topics must be a list of Title | Description lines')
    for field in ('headlines','introductions'):
        if not isinstance(profile.get(field),list) or not all(isinstance(s,str) for s in profile[field]):
            raise ValueError('Profile must include lists for '+field)
    with conn:
        payload=json.dumps(profile,ensure_ascii=False)
        existing=conn.execute('SELECT version,profile_json FROM profiles WHERE slug=?',(slug,)).fetchone()
        if existing and existing['profile_json']==payload:
            return existing['version']
        version=existing['version']+1 if existing else 1
        conn.execute('INSERT INTO profiles(slug,profile_json,version,updated_at) VALUES (?,?,?,?) '
                     'ON CONFLICT(slug) DO UPDATE SET profile_json=excluded.profile_json,version=excluded.version,updated_at=excluded.updated_at',
                     (slug,payload,version,now()))
        conn.execute('INSERT INTO profile_revisions(slug,version,profile_json,saved_at) VALUES (?,?,?,?)',
                     (slug,version,payload,now()))
        return version

def restore_profile_revision(conn,slug,version):
    row=conn.execute('SELECT profile_json FROM profile_revisions WHERE slug=? AND version=?',(slug,version)).fetchone()
    if not row:raise ValueError('Profile revision not found')
    return update_profile(conn,slug,json.loads(row['profile_json']))

def profile_history(conn,slug):
    return conn.execute('SELECT slug,version,saved_at FROM profile_revisions WHERE slug=? ORDER BY version DESC',(slug,)).fetchall()

def supplementary_for_business(conn,bid):
    return conn.execute('SELECT label,source_value,source_row FROM supplementary_fields WHERE business_id=? ORDER BY source_row',(bid,)).fetchall()

def validate_business(conn,bid):
    row=conn.execute('SELECT * FROM businesses WHERE id=?',(bid,)).fetchone()
    if not row:raise ValueError('Business not found')
    d=readable_business(row)
    issues=[]
    def add(severity,code,detail):issues.append({'severity':severity,'code':code,'detail':detail})
    if not val(d['name']):add('blocker','missing_name','Business identity is missing')
    if d['website_status']=='website_present':add('warning','existing_website','A website is listed for this business')
    if d['website_status']=='review':add('warning','uncertain_website','Website status requires review')
    if not (d['phone'] or d['whatsapp'] or d['email']):add('warning','no_contact','No usable phone, WhatsApp or email is available')
    if not safe_visible_description(d['description']):add('info','generic_description','Approved industry introduction will be used')
    if not safe_services(d['services']):add('info','industry_inspiration','Verified services unavailable; neutral industry inspiration will be used instead')
    if val(d['photos_raw']):add('info','photos_unverified','Business photo URLs are never embedded without explicit reuse rights')
    src=json.loads(row['source_json'])
    hours=val(src.get('Opening Hours'))
    if re.search(r'(diwali|hours might differ|holiday|temporarily)',hours,re.I):add('warning','temporary_hours','Temporary or holiday hours are not reliable regular opening hours')
    if val(src.get('Photo Reuse Rights')) and re.search('unverified|permission',val(src.get('Photo Reuse Rights')),re.I):
        add('warning','image_rights','Source photo usage status is unverified; Excel photo URLs are not automatically imported')
    with conn:
        conn.execute('INSERT INTO validation_reports VALUES (?,?,?) ON CONFLICT(business_id) DO UPDATE SET issue_json=excluded.issue_json,checked_at=excluded.checked_at',
                     (bid,json.dumps(issues,ensure_ascii=False),now()))
    return issues

def validate_rendered_html(document):
    soup=BeautifulSoup(document,'html.parser');issues=[]
    if not soup.select_one('html') or not soup.select_one('body'):issues.append('Missing document structure')
    if not soup.select_one('h1'):issues.append('Missing hero heading')
    if not soup.select_one('meta[name="viewport"]'):issues.append('Missing viewport meta tag')
    ids={node.get('id') for node in soup.select('[id]')}
    for a in soup.select('a[href^="#"]'):
        target=a.get('href','')[1:]
        if target and target not in ids:issues.append('Broken internal link: #'+target)
    for asset in soup.select('img[src],script[src],link[rel="stylesheet"][href]'):
        attribute='src' if asset.has_attr('src') else 'href'
        source=asset.get(attribute,'')
        if source.startswith(('http://','https://','//')):issues.append('External required asset: '+source[:100])
    if 'TAZORixx' in document:issues.append('Legacy brand name remains')
    if 'These are illustrative categories for the demo' in document:issues.append('Unreplaced demo service copy')
    if 'Gallery policy:' in document:issues.append('Unreplaced demo gallery policy')
    return sorted(set(issues))

def readable_business(b):
    d=dict(b)
    overrides=json.loads(d.get('content_override') or '{}')
    d.update({k:v for k,v in overrides.items() if v})
    return d


def valid_google_rating(value):
    """Parse a 0–5 Google rating from Excel; never infer a missing rating."""
    raw=val(value).strip()
    if not raw:return None
    match=re.fullmatch(r'([0-5](?:[.,]\d{1,2})?)(?:\s*/\s*5)?',raw)
    if not match:return None
    score=float(match.group(1).replace(',','.'))
    return score if 0 < score <= 5 else None

def clean_public_field(value, max_length=350):
    """Keep concise, printable source details; exclude scraped listing dumps."""
    raw=val(value)
    if not raw or len(raw)>max_length or any(c in raw for c in ('\x00','\ue413','\ue14d')):
        return ''
    return re.sub(r'\s+', ' ', raw).strip()


def regular_hours(value):
    """Extract weekday schedule rows; exclude holiday/temporary hours claims."""
    raw=val(value)
    if not raw:return []
    raw=raw.replace('\ue14d','\n').replace('\u2028','\n')
    days='Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday'
    parts=re.split(r'(?=(?:'+days+r'))',raw,flags=re.I)
    output=[]
    for part in parts:
        match=re.match(r'^('+days+r')',part.strip(),re.I)
        if not match:continue
        day=match.group(1).capitalize()
        text=part[match.end():].strip(' :,-')
        # Holiday entries are not reliable regular trading hours.
        if re.search(r'diwali|holiday|hours might differ|temporarily|special hours|balipratipada|bhai dooj',text,re.I):continue
        if re.search(r'closed|24\s*hours|\d{1,2}(?::\d{2})?\s*(?:am|pm)',text,re.I):
            output.append((day,clean_public_field(text,90)))
    return output[:7]


def valid_review_count(raw):
    value=val(raw).replace(',','')
    return int(value) if re.fullmatch(r'\d{1,8}',value) else None


def safe_maps_url(raw):
    url=val(raw)
    host=domain(url)
    return url if url_ok(url) and (host in ('google.com','maps.google.com','maps.app.goo.gl','goo.gl') or host.endswith('.google.com')) else ''


def append_section(soup, before, title, eyebrow, section_id, paragraphs=None):
    section=soup.new_tag('section',attrs={'class':'section section-alt tz-details','id':section_id})
    wrap=soup.new_tag('div',attrs={'class':'wrap'});section.append(wrap)
    top=soup.new_tag('div',attrs={'class':'section-top'});wrap.append(top)
    eye=soup.new_tag('span',attrs={'class':'eyebrow'});eye.string=eyebrow;top.append(eye)
    h=soup.new_tag('h2');h.string=title;top.append(h)
    for text in paragraphs or []:
        element=soup.new_tag('p');element.string=text;wrap.append(element)
    before.insert_before(section)
    return section,wrap


def add_svg_icon(soup, name):
    svg=soup.new_tag('svg',attrs={'viewBox':'0 0 24 24','width':'19','height':'19','fill':'none',
        'stroke':'currentColor','stroke-width':'2','stroke-linecap':'round','stroke-linejoin':'round','aria-hidden':'true'})
    paths={
        'map':['M20 10c0 5-8 12-8 12S4 15 4 10a8 8 0 1 1 16 0Z','M12 10a2.5 2.5 0 1 0 0 .01Z'],
        'clock':['M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18','M12 7v5l3 2'],
        'star':['m12 2 3.1 6.3 7 .9-5.1 4.9 1.2 6.9-7-3.7-7 3.7 1.2-6.9-5.1-4.9 7-.9Z'],
        'link':['M10 13a5 5 0 0 0 7 .4l3-3a5 5 0 0 0-7-7l-2 2','M14 11a5 5 0 0 0-7-.4l-3 3a5 5 0 0 0 7 7l2-2'],
        'social':['M12 2a10 10 0 1 0 0 20a10 10 0 1 0 0-20','M2 12h20','M12 2c3 3 3 17 0 20','M12 2c-3 3-3 17 0 20'],
    }
    for d in paths.get(name,paths['social']):svg.append(soup.new_tag('path',attrs={'d':d}))
    return svg


def add_social_icon(soup, label):
    """Small, self-contained platform pictograms (no external icon library)."""
    svg=soup.new_tag('svg',attrs={'viewBox':'0 0 24 24','width':'19','height':'19',
          'fill':'none','stroke':'currentColor','stroke-width':'1.8','stroke-linecap':'round',
          'stroke-linejoin':'round','aria-hidden':'true'})
    shapes={
        'Facebook':['M15 4h-2a3 3 0 0 0-3 3v13','M7 11h10'],
        'Instagram':['M7 3h10a4 4 0 0 1 4 4v10a4 4 0 0 1-4 4H7a4 4 0 0 1-4-4V7a4 4 0 0 1 4-4','M12 8a4 4 0 1 0 0 8a4 4 0 1 0 0-8','M17.5 6.5h.01'],
        'LinkedIn':['M4 10v10','M4 5v.01','M9 20v-10','M9 14a5 5 0 0 1 10 0v6'],
        'YouTube':['M3 7Q3 5 6 5h12q3 0 3 2v10q0 2-3 2H6q-3 0-3-2Z','m10 9 6 3-6 3Z'],
        'X / Twitter':['M3 3 21 21','M21 3 3 21','M7 3h4l10 18h-4Z'],
        'Telegram':['m3 11 18-8-5 18-5-7-8-3Z','m11 14 10-11'],
    }
    if label not in shapes:return add_svg_icon(soup,'link')
    for d in shapes[label]:svg.append(soup.new_tag('path',attrs={'d':d}))
    return svg

def business_extra_fields(source):
    result=[]
    columns=[('Business Highlights','Highlights'),('Service Areas','Service Areas'),
        ('Price / Price Range','Price Range'),('Payment Methods','Payment Methods'),
        ('Amenities','Amenities'),
        ('Service / Product Descriptions','Service / Product Information'),
        ('Online Booking / Order Links','Booking / Ordering'),
        ('Other Phone Numbers','Other Phone Numbers'),('City / Search Area','Area')]
    for key,title in columns:
        value=clean_public_field(source.get(key),350)
        if value and value.casefold() not in ('none','n/a','not available','call phone number'):
            result.append((title,value))
    return result

def set_text(el,text):
    if el is None:return
    el.clear();el.append(str(text))

def make_industry_icon(soup, slug, index, kind='services'):
    """Lightweight self-contained line icons; no web fonts or image downloads."""
    # Category-specific geometry (each service/process has distinct motifs).
    symbol_map = {
      'interior designers':['house','building','lamp','leaf'],
      'architects':['blueprint','building','ruler','house'],
      'real estate agencies':['house','keys','building','map'],
      'construction companies':['building','tools','ruler','check'],
      'manufacturers':['tools','gear','package','check'],
      'solar companies':['sun','bolt','house','check'],
      'coaching institutes':['book','graduation','pencil','check'],
      'furniture manufacturers':['chair','tools','ruler','package'],
      'event management companies':['calendar','star','people','check'],
      'travel agencies':['map','plane','suitcase','check'],
    }
    shapes={
      'house':['M3 11l9-8 9 8v10H3z','M9 21v-7h6v7'],
      'building':['M4 21V4h16v17','M8 8h2m4 0h2M8 12h2m4 0h2M8 16h2m4 0h2'],
      'lamp':['M7 12l3-8h4l3 8z','M12 12v7m-5 2h10'],
      'leaf':['M20 4C8 3 3 10 4 19c8 3 16-4 16-15Z','M5 19l12-12'],
      'blueprint':['M3 5h18v15H3z','M7 9h10M7 13h6m0 0v5'],
      'ruler':['M4 17 17 4l4 4-13 13z','m13-9 2 2m-6 2 2 2'],
      'keys':['M15 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8Z','M11 14l-8 8m2-5 2 2m0-4 2 2'],
      'map':['M3 6l6-3 6 3 6-3v15l-6 3-6-3-6 3z','M9 3v15m6-12v15'],
      'tools':['M4 20 13 11','M14 3a5 5 0 0 0 7 7l-6 6-7-7z','M3 17l4 4'],
      'check':['M4 12l5 5L20 6','M3 3h18v18H3z'],
      'gear':['M12 3v3m0 12v3M3 12h3m12 0h3M5.6 5.6l2.1 2.1m8.6 8.6 2.1 2.1m0-12.8-2.1 2.1m-8.6 8.6-2.1 2.1','M12 8a4 4 0 1 0 0 8a4 4 0 1 0 0-8'],
      'package':['M3 8l9-5 9 5v10l-9 4-9-4z','M3 8l9 5 9-5m-9 5v9'],
      'sun':['M12 3v3m0 12v3M3 12h3m12 0h3M5.5 5.5l2 2m9 9 2 2m0-13-2 2m-9 9-2 2','M12 8a4 4 0 1 0 0 8a4 4 0 1 0 0-8'],
      'bolt':['M13 2 5 13h7l-1 9 8-12h-7z'],
      'book':['M3 5c3-2 6-2 9 0 3-2 6-2 9 0v15c-3-2-6-2-9 0-3-2-6-2-9 0z','M12 5v15'],
      'graduation':['M2 9l10-5 10 5-10 5z','M6 12v5c4 3 8 3 12 0v-5'],
      'pencil':['M3 21l4-1 13-13-4-4L3 16z','m14-15 4 4'],
      'chair':['M5 4v9h14V4M5 13v8m14-8v8M5 17h14'],
      'calendar':['M3 5h18v16H3z','M7 2v6m10-6v6M3 10h18'],
      'star':['m12 2 3 7 7 1-5 5 1 7-6-4-6 4 1-7-5-5 7-1z'],
      'people':['M8 12a4 4 0 1 0 0-8 4 4 0 1 0 0 8M1 21v-3c0-5 14-5 14 0v3','M18 5a3 3 0 0 1 0 6m1 5c2 0 4 2 4 5'],
      'plane':['M2 13l20-9-7 18-4-8z','M11 14l11-10'],
      'suitcase':['M3 7h18v14H3z','M9 7V4h6v3m-3 0v14'],
    }
    # Process always uses process-specific themes, rather than repeating services.
    process_symbols=['blueprint','tools','check','keys']
    if kind=='process':
        category= {'solar companies':['sun','bolt','tools','check'],
                   'coaching institutes':['book','pencil','graduation','check'],
                   'travel agencies':['map','plane','suitcase','check']}.get(slug,process_symbols)
    else:category=symbol_map.get(slug,['house','tools','star','check'])
    symbol=category[index%len(category)]
    svg=soup.new_tag('svg',attrs={'class':'tz-industry-icon','viewBox':'0 0 24 24',
      'fill':'none','stroke':'currentColor','stroke-width':'1.5','stroke-linecap':'round',
      'stroke-linejoin':'round','aria-hidden':'true'})
    for path in shapes[symbol]:svg.append(soup.new_tag('path',attrs={'d':path}))
    return svg


def make_contact_icon(soup,kind):
    svg=soup.new_tag('svg',attrs={'viewBox':'0 0 24 24','width':'22','height':'22',
      'fill':'none','stroke':'currentColor','stroke-width':'1.8','stroke-linecap':'round',
      'stroke-linejoin':'round','aria-hidden':'true'})
    paths=(['M21 11.5a8.4 8.4 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.7A8.4 8.4 0 0 1 4 11.5 8.5 8.5 0 1 1 21 11.5Z',
           'M9 9.1c.3 2 2 3.7 4 4.1l1.4-1 1.8 1.2c-.7 2.3-2.5 2.4-4.1 1.7-2.5-1-4.3-3.2-4.8-5.5-.3-1.3.5-2 1.7-2.4z']
       if kind=='whatsapp' else
       ['M6.7 3.4l3.4 3.3-2 3a15 15 0 0 0 6.2 6.2l3-2 3.3 3.4-2.1 3c-.5.7-1.5.9-2.3.6A21 21 0 0 1 3.1 7.8c-.3-.8-.1-1.8.6-2.3z'])
    for path in paths:svg.append(soup.new_tag('path',attrs={'d':path}))
    return svg


def render_html(conn, business_id, template_path):
    b=conn.execute('SELECT * FROM businesses WHERE id=?',(business_id,)).fetchone()
    if b is None:raise ValueError('Business not found')
    d=readable_business(b)
    source=json.loads(b['source_json'] or '{}')
    slug,profile,version=get_profile(conn,b)
    # Editable Markdown updates apply to both fresh and existing SQLite profiles.
    markdown_entry = load_industry_markdown().get(slug)
    if markdown_entry:
        profile = {**profile, 'category': markdown_entry['category'],
                   'headlines': markdown_entry['headlines'] or profile.get('headlines', []),
                   'introductions': markdown_entry['introductions'] or profile.get('introductions', [])}
    key=business_id+':'+slug
    heading=clean_tagline(d['tagline']) or choice(profile.get('headlines',[]),key+'headline') or 'Welcome'
    intro=safe_visible_description(d['description']) or choice(profile.get('introductions',[]),key+'intro')
    soup=BeautifulSoup(Path(template_path).read_text(encoding='utf8'),'html.parser')
    name=d['name'];category=(d['industry'] or d['subtype'] or 'Local Business') if slug=='general' else (profile.get('category') or d['industry'] or 'Local Business')
    if soup.title:set_text(soup.title,name+' | '+category+' — TAZORIXX')
    meta=soup.select_one('meta[name="description"]')
    if meta:meta['content']=f'{name}. {intro}'[:250]
    # Explicitly remove all sample-only claims and text.
    banner=soup.select_one('.banner')
    if banner:
        # The preview warning is developer-facing and should not be on customer pages.
        banner.decompose()
    logo=soup.select_one('header .logo')
    if logo:logo.clear();logo.append(name);logo['aria-label']=name+' home';logo['title']=name
    logo_sub=soup.select_one('.logo-sub')
    if logo_sub:logo_sub.decompose() if logo_sub.parent != logo else None
    set_text(soup.select_one('.hero-copy .eyebrow'),category)
    set_text(soup.select_one('.hero-copy h1'),heading)
    set_text(soup.select_one('.hero-copy p'),intro)
    # Display genuine Google ratings imported from Excel, only when present.
    rating=valid_google_rating(d.get('rating'))
    reviews=valid_review_count(source.get('Google Review Count'))
    if rating is not None:
        hero_copy=soup.select_one('.hero-copy')
        if hero_copy:
            rating_block=soup.new_tag('div',attrs={'class':'tz-rating','aria-label':f'Google rating {rating:.1f} out of 5'})
            stars=soup.new_tag('span',attrs={'class':'tz-rating-stars','aria-hidden':'true'})
            stars.string='★' * int(rating) + '☆' * (5-int(rating))
            value=soup.new_tag('strong');value.string=f'{rating:.1f}/5'
            rating_source=soup.new_tag('span');rating_source.string='Google rating' + (f' · {reviews:,} reviews' if reviews is not None else '')
            rating_block.extend([stars,value,rating_source])
            heading_node=hero_copy.select_one('p')
            if heading_node:heading_node.insert_after(rating_block)
            else:hero_copy.append(rating_block)
    # The hero's primary action is directions, never the template's enquiry CTA.
    hero_buttons=soup.select_one('.hero-buttons')
    if hero_buttons:
        hero_buttons.clear()
        maps=safe_maps_url(d.get('maps_url'))
        if maps:
            button=soup.new_tag('a',attrs={'class':'btn btn-gold tz-directions','href':maps,
                'target':'_blank','rel':'noopener noreferrer'})
            button.append(add_svg_icon(soup,'map'))
            button.append(' View on Google Maps')
            hero_buttons.append(button)
    set_text(soup.select_one('.section-top h2'),'About '+name)
    # Keep real-business branding distinct from the template's furniture artwork.
    if slug not in ('interior designers','architects','furniture'):
        art=soup.select_one('.hero-art')
        if art:
            art.clear()
            art['aria-label']='Abstract decorative visual, not a business photograph'
            art['style']='background: radial-gradient(circle at 68% 30%, var(--a2), transparent 28%), linear-gradient(135deg,var(--c1),var(--c4),var(--a));'
            svg=soup.new_tag('svg',attrs={'viewBox':'0 0 560 425','aria-hidden':'true'})
            circle=soup.new_tag('circle',attrs={'cx':'330','cy':'210','r':'135','fill':'none','stroke':'rgba(255,255,255,.45)','stroke-width':'2'})
            svg.append(circle);art.append(svg)
    # Remove the template's hero illustration caption for all businesses.
    for caption in soup.select('.hero-caption'):caption.decompose()
    # Content may be sparse: render neutral industry inspiration instead of
    # claiming unverified offerings. Verified business services remain distinct.
    services=safe_services(d['services'])
    topics=markdown_topics(slug) or neutral_profile_content(conn,slug,profile)
    service_section=soup.select_one('#services')
    if service_section:
        if services:
            set_text(service_section.select_one('.section-top .eyebrow'),'Business information')
            set_text(service_section.select_one('.section-top h2'),'Listed Services & Offerings')
            set_text(service_section.select_one('.section-top p'),
                     'Explore what this business offers.')
        else:
            set_text(service_section.select_one('.section-top .eyebrow'),'Explore')
            set_text(service_section.select_one('.section-top h2'),'Common Services in '+category)
            # These are neutral topics, not assertions that the business provides services.
            section_intro=service_section.select_one('.section-top p')
            if section_intro:section_intro.decompose()
        grid=service_section.select_one('.service-grid')
        if grid:
            grid.clear()
            entries=[(item,'') for item in services] if services else topics
            for title,description in entries[:6]:
                card=soup.new_tag('article',attrs={'class':'service-card'})
                icon=soup.new_tag('svg',attrs={'class':'service-icon','viewBox':'0 0 48 48',
                                             'fill':'none','stroke':'currentColor','stroke-width':'2','aria-hidden':'true'})
                motif=soup.new_tag('path',attrs={'d':'M9 11h30v26H9zM15 18h18M15 25h13M15 31h18'})
                icon.append(motif);card.append(icon)
                h=soup.new_tag('h3');h.string=title;card.append(h)
                if description:
                    p=soup.new_tag('p');p.string=description;card.append(p)
                grid.append(card)
    # Industry-specific visual cards and a four-step customer-friendly workflow.
    # This is general category information, never represented as the business's
    # verified methodology or project portfolio.
    if service_section and service_section.select_one('.service-grid'):
        service_section['class'] = list(dict.fromkeys(service_section.get('class', []) + ['tz-visual-services']))
        for number,card in enumerate(service_section.select('.service-card'),1):
            card['class'] = list(dict.fromkeys(card.get('class',[]) + ['tz-service-tile']))
            icon=card.select_one('.service-icon')
            if icon:
                icon.decompose()
            card.insert(0, make_industry_icon(soup, slug, number-1, 'services'))
        # Keep the first four generic industry service topics in a balanced row.
        # Verified business offerings are never removed or replaced.
        if not services:
            for card in service_section.select('.service-card')[4:]:card.decompose()
    process_steps=markdown_topics(slug,'process')
    if process_steps:
        reference=soup.select_one('#business-details') or soup.select_one('#contact')
        if reference:
            process_section,process_wrap=append_section(soup, reference, 'How It Typically Works', '', 'process')
            process_section['class']=['section','tz-process-section']
            eyebrow=process_section.select_one('.eyebrow')
            if eyebrow:eyebrow.decompose()
            process_grid=soup.new_tag('div',attrs={'class':'tz-process-grid'})
            for idx,(step_title,step_description) in enumerate(process_steps[:4]):
                card=soup.new_tag('article',attrs={'class':'tz-process-card'})
                card.append(make_industry_icon(soup,slug,idx,'process'))
                title=soup.new_tag('h3');title.string=f'{idx+1}. {step_title}';card.append(title)
                description=soup.new_tag('p');description.string=step_description;card.append(description)
                process_grid.append(card)
            process_wrap.append(process_grid)
    # Industry planning content appears only if verified business content is
    # limited. This is helpful customer-facing category advice, not company claims.
    guide_topics = markdown_topics(slug, 'guide')
    if guide_topics and (not services or not safe_visible_description(d['description'])):
        guide_section = soup.new_tag('section', attrs={'class':'section tz-industry-guide', 'id':'industry-guide'})
        outer = soup.new_tag('div', attrs={'class':'wrap'})
        top = soup.new_tag('div', attrs={'class':'section-top'})
        eyebrow = soup.new_tag('span', attrs={'class':'eyebrow'}); eyebrow.string = 'Helpful Information'
        title = soup.new_tag('h2'); title.string = 'What to Consider'
        top.extend([eyebrow,title]); outer.append(top)
        grid = soup.new_tag('div',attrs={'class':'service-grid'})
        for topic_title, topic_description in guide_topics[:3]:
            card = soup.new_tag('article',attrs={'class':'service-card'})
            h = soup.new_tag('h3');h.string=topic_title
            para = soup.new_tag('p');para.string=topic_description
            card.extend([h,para]);grid.append(card)
        outer.append(grid);guide_section.append(outer)
        reference = soup.select_one('#business-details') or soup.select_one('#contact')
        if reference:reference.insert_before(guide_section)
        else:soup.select_one('main').append(guide_section)
    # A sparse listing can still have an informative ABOUT THE INDUSTRY
    # section, clearly separate from an actual About the Company statement.
    about=soup.select_one('#about')
    description=safe_visible_description(d['description'])
    if about:
        if description:
            set_text(about.select_one('.eyebrow'),'Business information')
            set_text(about.select_one('h2'),'About '+name)
            about_body=description
        else:
            # A generic industry filler is not the business's About section.
            # Remove the whole section when there is no sourced description.
            about.decompose()
            about=None
        paragraphs=about.select('.about-copy p') if about else []
        if paragraphs:set_text(paragraphs[0],about_body)
        for x in paragraphs[1:]:x.decompose()
        # Remove claims from the original furniture demo.
        for x in (about.select('.feature-line') if about else []):x.decompose()
        if about and slug not in ('interior designers','furniture'):
            art=about.select_one('.about-art')
            if art:
                art.clear();art.append(make_abstract_illustration(soup,0,category))
                art['aria-label']='Abstract industry-themed decoration, not business project photography'
    # Keep one prominent image: remove decorative About imagery for speed.
    for decorative in soup.select('#about .about-art'): decorative.decompose()
    about_grid=soup.select_one('#about .about-grid')
    if about_grid:about_grid['class']=['about-grid','tz-single-column']
    # Embed only explicitly approved local images (not scraped image URLs).
    hero_media,hero_origin=media_for_render(conn,business_id,slug,'hero')
    if not hero_media:
        # A user may supply gallery images only; the first approved image can
        # also fill the hero slot, without needing a second upload.
        hero_media,hero_origin=media_for_render(conn,business_id,slug,'gallery')
    if hero_media:
        art=soup.select_one('.hero-art')
        if art:
            art.clear()
            photo=soup.new_tag('img',src=image_src(hero_media[0]))
            photo['alt']=(('Approved photo supplied for '+name) if hero_origin=='business'
                          else ('Illustrative '+category+' imagery, not a photograph of '+name))
            photo['style']='width:100%;height:100%;object-fit:cover;display:block'
            art.append(photo)
            art['aria-label']=photo['alt']
            # No hero caption: image origin is retained in alt/aria-label for accessibility.
    # Only approved images can be presented as business or licensed industry
    # photographs. When no media was provided, use the master template's OWN
    # vector illustrations, clearly marked as generic inspiration, never a
    # gallery of the business's work. For unrelated categories use abstract SVGs.
    gallery=soup.select_one('#gallery')
    gallery_media,gallery_origin=media_for_render(conn,business_id,slug,'gallery')
    if gallery:
        grid=gallery.select_one('.gallery')
        if gallery_media:
            set_text(gallery.select_one('.section-top .eyebrow'),'Approved imagery')
            set_text(gallery.select_one('h2'),'Business Gallery' if gallery_origin=='business' else 'Industry Inspiration')
            set_text(gallery.select_one('.section-top p'),
                     'Photos' if gallery_origin=='business' else 'Design Ideas')
            if grid:
                grid.clear()
                for media in gallery_media[:2]:
                    tile=soup.new_tag('div',attrs={'class':'gallery-tile'})
                    image=soup.new_tag('img',src=image_src(media))
                    image['alt']='Authorized business photograph' if gallery_origin=='business' else 'Licensed illustrative industry image'
                    image['style']='width:100%;height:100%;object-fit:cover;display:block'
                    tile.append(image);grid.append(tile)
        else:
            # Avoid filler images: remove unconfigured galleries.
            gallery.decompose()
        for note in gallery.select('.demo-note'):note.decompose()
    # The template's strip describes demonstration design, not actual business facts.
    strip=soup.select_one('.strip')
    if strip:strip.decompose()
    # Compact verified, structured Excel details; no enormous scraper dumps.
    insertion=soup.select_one('#contact')
    if insertion:
        cards=business_extra_fields(source)
        if cards:
            details,wrap=append_section(soup,insertion,'Business Details','','business-details')
            details['class']=['section','tz-details','tz-compact-details']
            eyebrow=details.select_one('.eyebrow')
            if eyebrow:eyebrow.decompose()
            dl=soup.new_tag('div',attrs={'class':'tz-info-grid'});wrap.append(dl)
            for label,value in cards:
                item=soup.new_tag('div',attrs={'class':'tz-info-item'})
                icon=soup.new_tag('span',attrs={'class':'tz-info-symbol','aria-hidden':'true'})
                icon.string={'Highlights':'✦','Service Areas':'⌖','Price Range':'₹','Payment Methods':'◈',
                             'Amenities':'✧','Service / Product Information':'◇',
                             'Booking / Ordering':'↗','Other Phone Numbers':'☎','Area':'⌖'}.get(label,'✧')
                item.append(icon)
                content=soup.new_tag('div',attrs={'class':'tz-info-content'});item.append(content)
                key=soup.new_tag('strong');key.string=label;content.append(key)
                if url_ok(value):
                    a=soup.new_tag('a',href=value);a.string='Open '+label
                    a['target']='_blank';a['rel']='noopener noreferrer';content.append(a)
                else:
                    p=soup.new_tag('span');p.string=value;content.append(p)
                dl.append(item)
        hours=regular_hours(source.get('Opening Hours'))
        if hours:
            hrs,wrap=append_section(soup,insertion,'Opening Hours','','hours')
            eyebrow=hrs.select_one('.eyebrow')
            if eyebrow:eyebrow.decompose()
            timetable=soup.new_tag('div',attrs={'class':'tz-hours'})
            for group_name,days in [('Mon–Sat',{'Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'}),
                                    ('Sun',{'Sunday'})]:
                group=[(day,time) for day,time in hours if day in days]
                if not group:continue
                block=soup.new_tag('div',attrs={'class':'tz-hours-group'})
                title=soup.new_tag('h3');title.append(add_svg_icon(soup,'clock'));title.append(' '+group_name)
                block.append(title)
                # Display a single compact time for each grouping instead of day-by-day rows.
                # Differing observed schedules must not be incorrectly merged into one time range.
                unique_times=list(dict.fromkeys(time.strip() for _,time in group if time.strip()))
                display=unique_times[0] if len(unique_times)==1 else 'Hours vary: '+ ' / '.join(unique_times)
                value=soup.new_tag('p',attrs={'class':'tz-hours-value'})
                value.string=display
                block.append(value);timetable.append(block)
            wrap.append(timetable)
    contact=soup.select_one('#contact')
    if contact:
        set_text(contact.select_one('h2'),'Contact '+name)
        set_text(contact.select_one('p'),'Get in touch using the details below.')
        point_values=[]
        if d['phone']:point_values.append(('Phone',d['phone']))
        if d['whatsapp']:point_values.append(('WhatsApp',d['whatsapp']))
        if d['email']:point_values.append(('Email',d['email']))
        if d['address']:point_values.append(('Address',d['address']))
        if safe_maps_url(d.get('maps_url')):point_values.append(('Google Maps',safe_maps_url(d['maps_url'])))
        for point in contact.select('.contact-point'):point.decompose()
        anchor=contact.select_one('.fineprint')
        if anchor:
            anchor.decompose()
            anchor=None
        for lbl,value in point_values:
                p=soup.new_tag('div',attrs={'class':'contact-point'})
                body=soup.new_tag('div');small=soup.new_tag('small');small.string=lbl;body.append(small)
                if lbl=='Phone':
                    a=soup.new_tag('a',href='tel:'+d['phone']);a.string=value;body.append(a)
                elif lbl=='WhatsApp':
                    a=soup.new_tag('a',href='https://wa.me/'+re.sub(r'\D','',d['whatsapp']));a.string='Open WhatsApp';body.append(a)
                elif lbl=='Email':
                    a=soup.new_tag('a',href='mailto:'+d['email']);a.string=value;body.append(a)
                elif lbl=='Google Maps':
                    a=soup.new_tag('a',href=value);a['target']='_blank';a['rel']='noopener noreferrer';a.append(add_svg_icon(soup,'map'));a.append(' Directions');body.append(a)
                else:
                    body.append(value)
                p.append(body)
                contact.select_one('.contact-grid > div:first-child').append(p)
        form=contact.select_one('#enquiry')
        if form and not (d['email'] or d['whatsapp']):form.decompose()
        elif form:
            set_text(form.select_one('p'),'Fill in your enquiry and open it in your WhatsApp or email app. You must send it yourself.')
            for id_,exists,txt in [('waSend',bool(d['whatsapp']),'Open WhatsApp ↗'),('mailSend',bool(d['email']),'Open Email ↗')]:
                btn=form.select_one('#'+id_)
                if btn:
                    if not exists:btn.decompose()
                    else:set_text(btn,txt)
            note=form.select_one('.form-note')
            if note:set_text(note,'Your device opens the selected app with a prepared message. Nothing is automatically sent.')
        social_fields=[('Facebook','facebook.com'),('Instagram','instagram.com'),('LinkedIn','linkedin.com'),
            ('YouTube','youtube.com'),('X / Twitter','x.com'),('Telegram','t.me')]
        links=[]
        for platform,host in social_fields:
            url=val(source.get(platform)); actual=domain(url)
            hosts={'facebook.com':('facebook.com','fb.com'),'instagram.com':('instagram.com',),
                'linkedin.com':('linkedin.com',),'youtube.com':('youtube.com','youtu.be'),
                'x.com':('x.com','twitter.com'),'t.me':('t.me','telegram.me')}[host]
            if url_ok(url) and any(actual==h or actual.endswith('.'+h) for h in hosts):links.append((platform,url))
        logo_url=val(source.get('Business Logo URL'))
        if url_ok(logo_url): links.append(('Logo URL',logo_url))
        if links and soup.select_one('#contact .contact-grid > div:first-child'):
            contact_panel=soup.select_one('#contact .contact-grid > div:first-child')
            bar=soup.new_tag('div',attrs={'class':'tz-social-links','id':'social-links','aria-label':'Social media and business links'})
            if contact_panel:contact_panel.append(bar)
            for label,url in links:
                a=soup.new_tag('a',attrs={'href':url,'target':'_blank','rel':'noopener noreferrer','class':'tz-social'})
                a.append(add_social_icon(soup,label))
                a.append(' '+label);bar.append(a)
    footer=soup.select_one('footer')
    if footer:
        footer_logo=footer.select_one('.logo')
        if footer_logo:set_text(footer_logo,name)
        for x in footer.select('.logo-sub'):x.decompose()
        para=footer.select_one('.footer-main p')
        if para:para.decompose()
        bottom=footer.select_one('.footer-bottom span')
        if bottom:
            bottom.clear();bottom.append('© ')
            yr=soup.new_tag('span',id='year');bottom.append(yr);bottom.append(' '+name)
    # Keep business contact actions inside the Contact section.
    # Only a phone actually listed as WhatsApp receives a WhatsApp action;
    # a generic phone number is not evidence of WhatsApp availability.
    quick=soup.new_tag('div',attrs={'class':'tz-quick-contact','aria-label':'Quick contact actions'})
    if d['whatsapp']:
        w=soup.new_tag('a',attrs={'class':'tz-quick-action tz-quick-whatsapp',
            'href':'https://wa.me/'+re.sub(r'\D','',d['whatsapp']),
            'target':'_blank','rel':'noopener noreferrer','aria-label':'Message on WhatsApp','title':'Message on WhatsApp'})
        w.append(make_contact_icon(soup,'whatsapp'))
        label=soup.new_tag('span',attrs={'class':'tz-quick-label'});label.string='WhatsApp';w.append(label)
        quick.append(w)
    if d['phone']:
        c=soup.new_tag('a',attrs={'class':'tz-quick-action tz-quick-call',
            'href':'tel:'+d['phone'],'aria-label':'Call business','title':'Call business'})
        c.append(make_contact_icon(soup,'phone'))
        label=soup.new_tag('span',attrs={'class':'tz-quick-label'});label.string='Call';c.append(label)
        quick.append(c)
    # Also show clearly labeled primary action buttons in the Contact section.
    if contact and quick.contents:
        panel=contact.select_one('.contact-grid > div:first-child')
        if panel:
            direct=soup.new_tag('div',attrs={'class':'tz-contact-actions'})
            for action in quick.select('a'):
                a=soup.new_tag('a',attrs={'class':'btn tz-contact-btn','href':action['href']})
                if action.has_attr('target'):
                    a['target']=action['target'];a['rel']=action['rel']
                a.append(make_contact_icon(soup,'whatsapp' if 'whatsapp' in action['class'] else 'phone'))
                a.append(' Message on WhatsApp' if 'whatsapp' in action['class'] else ' Call Now')
                direct.append(a)
            panel.append(direct)
    # Navigation corresponds only to actual rendered sections.
    nav=soup.select_one('header .navlinks')
    if nav:
        nav.clear()
        for section_id,label in [('home','Home'),('services','Offerings'),('about','About'),
                                 ('business-details','Details'),('process','Process'),('industry-guide','Guide'),('hours','Hours'),('contact','Contact')]:
            if soup.select_one('#'+section_id):
                link=soup.new_tag('a',href='#'+section_id);link.string=label;nav.append(link)
    # Remove links to optional sections omitted for speed.
    existing_ids={node.get('id') for node in soup.select('[id]')}
    for a in soup.select('a[href^="#"]'):
        if a.get('href','')[1:] not in existing_ids:
            a.decompose()
    # Lightweight add-on stylesheet remains embedded, keeping output standalone.
    style=soup.new_tag('style')
    style.string="""
    .tz-details{background:var(--bg2)}.tz-single-column{display:block;max-width:780px;margin:auto}
    .tz-compact-details{padding:42px 0 46px;background:linear-gradient(145deg,var(--bg),var(--bg2))}
    .tz-compact-details .section-top{margin-bottom:20px;text-align:left;max-width:none}
    .tz-compact-details h2{font-size:clamp(1.6rem,3vw,2.2rem);margin:0}
    .tz-info-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(230px,100%),1fr));gap:11px}
    .tz-info-item{display:flex;align-items:flex-start;gap:12px;padding:15px;background:var(--bg);border:1px solid var(--ln);border-radius:16px;overflow-wrap:anywhere;box-shadow:0 5px 18px rgba(var(--c1r),.045);transition:transform .25s,box-shadow .25s}
    .tz-info-item:hover{transform:translateY(-3px);box-shadow:0 12px 26px rgba(var(--c1r),.09)}
    .tz-info-symbol{display:grid!important;place-items:center;flex:0 0 35px;width:35px;height:35px;border-radius:12px;background:var(--t1);color:var(--c1);font-size:1.05rem}
    .tz-info-content{min-width:0;flex:1}.tz-info-content strong,.tz-info-content span{display:block}
    .tz-info-item strong{color:var(--c1);font-size:.79rem;margin-bottom:3px}
    .tz-info-content span,.tz-info-content a{font-size:.84rem;line-height:1.5}
    .tz-info-item a,.tz-social{color:var(--c2);text-decoration:underline;text-underline-offset:3px}
    .tz-hours{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(230px,100%),1fr));gap:14px;max-width:820px;margin:auto}
    .tz-hours-group{background:var(--bg);border:1px solid var(--ln);border-radius:18px;padding:19px 21px;box-shadow:0 7px 25px rgba(var(--c1r),.06)}
    .tz-hours-group h3{display:flex;align-items:center;gap:9px;color:var(--c1);font-size:1.02rem;margin-bottom:11px}
    .tz-hours-group svg{flex:none;color:var(--a)}.tz-hours ul{margin:0;padding:0;list-style:none}
    .tz-hours li{display:flex;gap:15px;justify-content:space-between;padding:7px 0;border-bottom:1px solid var(--ln);font-size:.86rem}
    .tz-hours li:last-child{border-bottom:0}
    .tz-hours-value{margin:0;color:var(--c1);font-size:1.04rem;font-weight:700;line-height:1.5;overflow-wrap:anywhere}
    .tz-hours-group{display:flex;flex-direction:column;justify-content:center;min-height:120px}
    .tz-hours-group h3{margin-bottom:8px}
    #contact .tz-social-links{display:flex;flex-wrap:wrap;gap:9px;justify-content:flex-start;margin-top:22px}
    #contact .tz-social{display:inline-flex;align-items:center;gap:7px;padding:10px 14px;background:rgba(255,255,255,.10);color:#fff;border:1px solid rgba(255,255,255,.25);border-radius:40px;font-size:.84rem;font-weight:600;text-decoration:none}
    #contact .tz-social:hover{transform:translateY(-2px);background:rgba(255,255,255,.2)}
    /* A gentle swinging hanging frame, with entrance and reduced-motion support. */
    .hero-art{transform-origin:50% -16px;animation:tz-hang-enter .85s ease-out both,tz-hang-swing 5.8s ease-in-out .9s infinite alternate!important}
    @keyframes tz-hang-enter{from{opacity:0;transform:translateY(16px) rotate(-2deg)}to{opacity:1;transform:translateY(0) rotate(0)}}
    @keyframes tz-hang-swing{from{transform:rotate(-2.2deg)}to{transform:rotate(2.2deg)}}
    @media(prefers-reduced-motion:reduce){.hero-art{animation:none!important;transform:none!important}}
    .tz-directions{gap:9px}.tz-directions svg{flex:none}
    /* Keep the desktop header a single horizontal row, even with long names. */
    .header .nav{display:flex;flex-wrap:nowrap;gap:clamp(9px,1.15vw,19px);min-width:0;min-height:72px}
    .header .logo{flex:1 1 auto;min-width:0;max-width:100%;font-size:clamp(.9rem,1.28vw,1.38rem);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;line-height:1.25}
    .header .navlinks{flex:0 0 auto;display:flex;flex-wrap:nowrap;align-items:center;gap:clamp(8px,1vw,17px);white-space:nowrap;font-size:clamp(.72rem,.82vw,.84rem)}
    .header .nav-cta{flex:0 0 auto;white-space:nowrap;padding:11px 15px;display:inline-flex;font-size:.78rem}
    .header .navlinks a{white-space:nowrap}
    @media(max-width:1000px){
        .header .logo{font-size:clamp(.83rem,1.4vw,1.1rem)}
        .header .navlinks{gap:9px;font-size:.75rem}
        .header .nav-cta{padding:9px 12px}
    }
    @media(max-width:760px){
        .header .nav{min-height:68px}
        .header .logo{font-size:clamp(.87rem,2.75vw,1.25rem)}
        .header .navlinks{display:none;position:absolute;top:100%;left:0;right:0;padding:20px;flex-direction:column;align-items:stretch;white-space:normal;gap:13px;background:var(--ivory);border-bottom:1px solid var(--ln);box-shadow:0 12px 25px #0002}
        .header .navlinks.open{display:flex}
        .header .nav-cta{display:none}
        .header .menu{display:block;flex:none}
    }
    @media(max-width:680px){.tz-hours{padding:15px}.tz-info-grid{grid-template-columns:1fr}}
    
    """
    style.string += """
    /* MVP 17: screenshot-inspired four-column editorial services and process. */
    .tz-visual-services .service-grid,.tz-process-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:22px}
    .tz-visual-services .tz-service-tile{border:1px solid var(--ln);border-radius:8px;padding:28px 22px;background:var(--bg);box-shadow:none;min-width:0}
    .tz-visual-services .tz-service-tile h3{font-size:clamp(1rem,1.35vw,1.25rem);color:var(--c1);margin:0 0 14px}
    .tz-visual-services .tz-service-tile p{font-size:.91rem;line-height:1.65;overflow-wrap:anywhere}
    .tz-industry-icon{width:67px;height:67px;color:var(--a);margin-bottom:20px;display:block}
    .tz-process-section{background:var(--bg);padding:58px 0 70px}
    .tz-process-section .section-top{text-align:left;max-width:none;margin:0 0 30px}
    .tz-process-section h2{font-size:clamp(2rem,3.5vw,3.2rem)}
    .tz-process-card{padding:12px 8px 12px 0;min-width:0}
    .tz-process-card h3{color:var(--c1);font-size:1.1rem;font-weight:700;margin:0 0 12px}
    .tz-process-card p{margin:0;color:var(--muted);font-size:.91rem;line-height:1.65;overflow-wrap:anywhere}
    .tz-process-card .tz-industry-icon{width:72px;height:72px}
    .tz-contact-actions{display:flex;flex-wrap:wrap;gap:12px;margin:23px 0}
    .tz-contact-btn{display:inline-flex;align-items:center;gap:10px}
    @media(max-width:1000px){.tz-visual-services .service-grid,.tz-process-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}}
    @media(max-width:550px){.tz-visual-services .service-grid,.tz-process-grid{grid-template-columns:1fr;gap:14px}.tz-process-card{padding:14px 0;border-bottom:1px solid var(--ln)}.tz-process-card .tz-industry-icon{margin-bottom:10px;width:56px;height:56px}}
    """
    soup.head.append(style)
    # Avoid stale demo modal, floating actions and all demo-only JavaScript.
    for elem in soup.select('.modal,.toast,.floating'):
        elem.decompose()
    # Existing JS contains bindings to removed demo elements. Strip only that script.
    for script in soup.find_all('script'):
        if script.string and "const $=id=>document.getElementById(id);const header=" in script.string:
            script.decompose()
    # Add safe site-specific mobile nav and hybrid enquiry handling.
    script=soup.new_tag('script')
    config={'wa':re.sub(r'\D','',d['whatsapp']), 'email':d['email']}
    script.string='''(()=>{'use strict';const cfg=CONFIG;const nav=document.querySelector('#navlinks'),menu=document.querySelector('#menu');
      if(menu&&nav){menu.addEventListener('click',()=>{const show=nav.classList.toggle('open');menu.setAttribute('aria-expanded',String(show))});nav.querySelectorAll('a').forEach(a=>a.addEventListener('click',()=>nav.classList.remove('open')))}
      const year=document.querySelector('#year');if(year)year.textContent=new Date().getFullYear();
      const form=document.querySelector('#enquiry');if(!form)return;
      function query(){const name=document.querySelector('#person').value.trim();const msg=document.querySelector('#message').value.trim();const info=document.querySelector('#contactInfo').value.trim();if(!name||!msg){form.reportValidity();return null}return `Hello, my name is ${name}.\\nContact: ${info||'Not provided'}\\nEnquiry: ${msg}`}
      const wa=document.querySelector('#waSend');if(wa&&cfg.wa)wa.addEventListener('click',()=>{const m=query();if(m)location.href='https://wa.me/'+cfg.wa+'?text='+encodeURIComponent(m)});
      const mail=document.querySelector('#mailSend');if(mail&&cfg.email)mail.addEventListener('click',()=>{const m=query();if(m)location.href='mailto:'+cfg.email+'?subject='+encodeURIComponent('Business enquiry')+'&body='+encodeURIComponent(m)});
    })();'''.replace('CONFIG',json.dumps(config,ensure_ascii=False).replace('</','<\\/'))
    soup.body.append(script)
    from demo_tracking import add_contact_panel
    add_contact_panel(soup, business_id, name)
    # Keep all seven theme controls and existing animation scripts intact.
    result=str(soup)
    if 'TAZORixx' in result:raise ValueError('Old brand name detected')
    return result,slug,version

def export_business(conn,bid,template_path,export_dir,overwrite=False):
    b=conn.execute('SELECT * FROM businesses WHERE id=?',(bid,)).fetchone()
    if not b:raise ValueError('Business record missing')
    if b['website_status'] not in ('missing','social_only'):
        raise ValueError('Only businesses without independent websites can be generated (missing or social-only).')
    export_dir=Path(export_dir);export_dir.mkdir(parents=True,exist_ok=True)
    safe=re.sub(r'[^a-zA-Z0-9_-]+','-',b['name']).strip('-')[:65] or 'business'
    suffix=hashlib.sha256(bid.encode()).hexdigest()[:9]
    path=export_dir/(safe+'-'+suffix+'.html')
    if path.exists() and not overwrite:raise FileExistsError(f'Existing output: {path.name}. Enable overwrite explicitly.')
    site,slug,version=render_html(conn,bid,template_path)
    from demo_tracking import click_url
    source_hash=hashlib.sha256((click_url()+b['source_json']+b['content_override']+slug+str(version)+RENDERER_VERSION+hashlib.sha256((_industry_markdown_path().read_bytes() if _industry_markdown_path() else b'')).hexdigest()+hashlib.sha256(Path(template_path).read_bytes()).hexdigest()+''.join(hashlib.sha256(row['image_bytes']).hexdigest() for role in ('hero','gallery') for row in media_for_render(conn,bid,slug,role)[0])).encode()).hexdigest()
    issues=validate_rendered_html(site)
    if issues:raise ValueError('Export validation failed: '+'; '.join(issues[:5]))
    # Stage a fully validated website in the SAME directory. For normal exports,
    # Windows rename and POSIX hard links both fail if the destination exists.
    # This avoids a race where a file appears after the initial existence check.
    fd, staged = tempfile.mkstemp(prefix='.tazorixx-',suffix='.tmp',dir=export_dir)
    try:
        with os.fdopen(fd,'w',encoding='utf8',newline='\n') as out:
            out.write(site)
            out.flush()
            os.fsync(out.fileno())
        if overwrite:
            os.replace(staged,path)
        else:
            try:
                if os.name == 'nt':
                    os.rename(staged,path)
                else:
                    os.link(staged,path)
            except FileExistsError:
                raise FileExistsError(f'Existing output: {path.name}. Enable overwrite explicitly.')
    finally:
        Path(staged).unlink(missing_ok=True)
    with conn:
        if overwrite:
            conn.execute("UPDATE client_reviews SET review_status='needs_review',reviewed_html_hash='',updated_at=? WHERE business_id=?",(now(),bid))
        conn.execute('INSERT INTO generation(business_id,profile_slug,profile_version,source_fingerprint,output_path,html_hash,exported_at) VALUES (?,?,?,?,?,?,?) ON CONFLICT(business_id) DO UPDATE SET profile_slug=excluded.profile_slug,profile_version=excluded.profile_version,source_fingerprint=excluded.source_fingerprint,output_path=excluded.output_path,html_hash=excluded.html_hash,exported_at=excluded.exported_at',
                     (bid,slug,version,source_hash,str(path),hashlib.sha256(site.encode()).hexdigest(),now()))
    return path


def backup_database(conn, directory):
    """Create a transactionally consistent SQLite snapshot."""
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    stem=datetime.now(timezone.utc).strftime('tazorixx-backup-%Y%m%d-%H%M%S-%f.sqlite3')
    target=directory/stem
    with sqlite3.connect(target) as dest:
        conn.backup(dest)
        result=dest.execute('PRAGMA integrity_check').fetchone()[0]
        if result!='ok':
            target.unlink(missing_ok=True)
            raise RuntimeError('Backup integrity check failed')
    return target


# Phase 3: persistent, resumable generation jobs. The UI processes one item per event-loop tick.
def create_generation_job(conn, business_ids, template_path, export_dir):
    ids=list(dict.fromkeys(business_ids))
    if not ids:raise ValueError('No businesses selected')
    for bid in ids:
        business=conn.execute('SELECT website_status FROM businesses WHERE id=?',(bid,)).fetchone()
        if not business:raise ValueError('Missing business record: '+bid)
        if business['website_status'] not in ('missing','social_only'):
            raise ValueError('Batch generation only accepts no-website leads.')
    with conn:
        cur=conn.execute('INSERT INTO generation_jobs(state,created_at,updated_at,export_dir,template_path,total) VALUES (?,?,?,?,?,?)',
                         ('paused',now(),now(),str(export_dir),str(template_path),len(ids)))
        job=cur.lastrowid
        conn.executemany('INSERT INTO generation_job_items(job_id,business_id) VALUES (?,?)',[(job,x) for x in ids])
    return job

def generation_job_summary(conn,job_id):
    job=conn.execute('SELECT * FROM generation_jobs WHERE id=?',(job_id,)).fetchone()
    if not job:raise ValueError('Job not found')
    counts={r['state']:r['count'] for r in conn.execute('SELECT state,COUNT(*) count FROM generation_job_items WHERE job_id=? GROUP BY state',(job_id,))}
    return {'id':job_id,'state':job['state'],'total':job['total'],'counts':counts,
            'export_dir':job['export_dir'],'updated_at':job['updated_at']}

def set_generation_job_state(conn,job_id,state):
    if state not in ('paused','running'):raise ValueError('Unsupported job state')
    with conn:
        if not conn.execute('SELECT 1 FROM generation_jobs WHERE id=?',(job_id,)).fetchone():raise ValueError('Job not found')
        conn.execute('UPDATE generation_jobs SET state=?,updated_at=? WHERE id=?',(state,now(),job_id))

def process_generation_step(conn,job_id):
    """Processes at most one pending item and commits its result for crash recovery."""
    job=conn.execute('SELECT * FROM generation_jobs WHERE id=?',(job_id,)).fetchone()
    if not job:raise ValueError('Job not found')
    if job['state']!='running':return None
    item=conn.execute("SELECT * FROM generation_job_items WHERE job_id=? AND state='pending' ORDER BY rowid LIMIT 1",(job_id,)).fetchone()
    if not item:
        with conn:conn.execute('UPDATE generation_jobs SET state=?,updated_at=? WHERE id=?',('completed',now(),job_id))
        return None
    try:
        output=export_business(conn,item['business_id'],job['template_path'],job['export_dir'],overwrite=False)
        state='completed';error=''
    except FileExistsError as exc:
        output=None;state='skipped';error=str(exc)
    except Exception as exc:
        output=None;state='failed';error=str(exc)[:1000]
    with conn:
        conn.execute('UPDATE generation_job_items SET state=?,attempts=attempts+1,last_error=?,output_path=? WHERE job_id=? AND business_id=?',
                     (state,error,str(output or ''),job_id,item['business_id']))
        conn.execute('UPDATE generation_jobs SET updated_at=? WHERE id=?',(now(),job_id))
    return {'business_id':item['business_id'],'state':state,'error':error,'path':str(output or '')}

def retry_failed_generation(conn,job_id):
    with conn:
        cur=conn.execute("UPDATE generation_job_items SET state='pending',last_error='' WHERE job_id=? AND state='failed'",(job_id,))
        conn.execute('UPDATE generation_jobs SET state=?,updated_at=? WHERE id=?',('paused',now(),job_id))
    return cur.rowcount


# Phase 7: preview, explicit client approval, immutable export validation and safe delivery ZIPs.
REVIEW_STATES = ('needs_review', 'needs_editing', 'approved')

def website_quality_report(conn, business_id):
    """Examine the most recently exported file, not just an in-memory preview."""
    business = conn.execute('SELECT * FROM businesses WHERE id=?', (business_id,)).fetchone()
    if not business:
        raise ValueError('Business not found')
    report = {'business_id': business_id, 'business_name': business['name'],
              'status': 'blocked', 'checks': [], 'warnings': [], 'errors': [],
              'output_path': '', 'actual_sha256': '', 'exported_at': ''}
    def problem(level, code, detail):
        record = {'code': code, 'detail': detail}
        report['checks'].append({'result': level, **record})
        (report['errors'] if level == 'error' else report['warnings']).append(record)
    if business['website_status'] not in ('missing', 'social_only'):
        problem('error', 'website_exists', 'Business is not eligible for a new website')
    generation = conn.execute('SELECT * FROM generation WHERE business_id=?', (business_id,)).fetchone()
    if not generation:
        problem('error', 'not_generated', 'Generate the website before review')
    else:
        path = Path(generation['output_path'])
        report['output_path'] = str(path)
        report['exported_at'] = generation['exported_at']
        if not path.is_file():
            problem('error', 'missing_file', 'The exported HTML file cannot be found')
        else:
            document = path.read_text(encoding='utf8')
            digest = hashlib.sha256(document.encode('utf8')).hexdigest()
            report['actual_sha256'] = digest
            if digest != generation['html_hash']:
                problem('error', 'modified_export', 'HTML differs from the last generated version')
            for issue in validate_rendered_html(document):
                problem('error', 'invalid_html', issue)
            soup = BeautifulSoup(document, 'html.parser')
            if not soup.select_one('#contact'):
                problem('error', 'missing_contact_section', 'Contact section is absent')
            if not soup.select_one('footer'):
                problem('error', 'missing_footer', 'Footer is absent')
            if len(document.encode('utf8')) > 3_000_000:
                problem('warning', 'large_html', 'HTML exceeds 3 MB; review embedded media')
    if not (business['phone'] or business['email'] or business['whatsapp']):
        problem('warning', 'no_direct_contact', 'No callable or email contact detail available')
    if not business['maps_url'] or not safe_maps_url(business['maps_url']):
        problem('warning', 'missing_maps', 'No valid Google Maps destination available')
    if not report['errors']:
        report['status'] = 'ready_with_warnings' if report['warnings'] else 'passed'
    return report


def list_client_previews(conn, search=''):
    """Return completed exports with persisted review state and computed QC status."""
    rows = conn.execute("""SELECT b.id,b.name,b.industry,b.website_status,
        g.output_path,g.html_hash,g.exported_at,
        COALESCE(r.review_status,'needs_review') review_status,
        COALESCE(r.reviewer_notes,'') reviewer_notes,
        COALESCE(r.reviewed_html_hash,'') reviewed_html_hash
        FROM generation g JOIN businesses b ON g.business_id=b.id
        LEFT JOIN client_reviews r ON r.business_id=b.id
        ORDER BY g.exported_at DESC,b.name""").fetchall()
    result=[]
    for row in rows:
        if search and search.casefold() not in (row['name']+' '+row['industry']).casefold():
            continue
        entry=dict(row)
        quality=website_quality_report(conn,row['id'])
        entry['quality_status']=quality['status']
        entry['approval_current']=(entry['review_status']=='approved'
                                   and entry['reviewed_html_hash']==quality['actual_sha256']
                                   and quality['status'] in ('passed','ready_with_warnings'))
        result.append(entry)
    return result


def set_client_review(conn,business_id,state,notes=''):
    if state not in REVIEW_STATES:
        raise ValueError('Unsupported review state')
    report=website_quality_report(conn,business_id)
    if state=='approved' and report['errors']:
        raise ValueError('Cannot approve: '+ '; '.join(x['detail'] for x in report['errors']))
    if len(notes)>2000:
        raise ValueError('Review notes must be shorter than 2001 characters')
    digest=report['actual_sha256'] if state=='approved' else ''
    with conn:
        conn.execute("""INSERT INTO client_reviews
            (business_id,review_status,reviewer_notes,reviewed_html_hash,updated_at)
            VALUES (?,?,?,?,?) ON CONFLICT(business_id) DO UPDATE SET
            review_status=excluded.review_status,reviewer_notes=excluded.reviewer_notes,
            reviewed_html_hash=excluded.reviewed_html_hash,updated_at=excluded.updated_at""",
            (business_id,state,notes,digest,now()))
    return report


def make_client_delivery_zip(conn,business_ids,zip_path):
    """Create a review-gated, atomic ZIP containing standalone HTML and factual summaries."""
    ids=list(dict.fromkeys(business_ids))
    if not ids:
        raise ValueError('Select at least one approved website')
    zip_path=Path(zip_path)
    if zip_path.exists():
        raise FileExistsError('A delivery package already exists at this path; choose a new filename')
    zip_path.parent.mkdir(parents=True,exist_ok=True)
    prepared=[]
    for bid in ids:
        business=conn.execute('SELECT * FROM businesses WHERE id=?',(bid,)).fetchone()
        if not business:
            raise ValueError('Business missing: '+str(bid))
        review=conn.execute('SELECT * FROM client_reviews WHERE business_id=?',(bid,)).fetchone()
        quality=website_quality_report(conn,bid)
        if not review or review['review_status']!='approved' or quality['errors'] or quality['actual_sha256'] != review['reviewed_html_hash']:
            raise ValueError('Website needs current approval before delivery: '+business['name'])
        source=Path(quality['output_path'])
        slug=re.sub(r'[^a-zA-Z0-9_-]+','-',business['name']).strip('-')[:50] or 'business'
        slug += '-' + hashlib.sha256(bid.encode()).hexdigest()[:8]
        document=source.read_bytes()
        if hashlib.sha256(document).hexdigest()!=quality['actual_sha256']:
            raise ValueError('Website changed while packaging: '+business['name'])
        prepared.append((business,quality,slug,document))
    fd,stage=tempfile.mkstemp(prefix='.tazorixx-delivery-',suffix='.zip.tmp',dir=zip_path.parent)
    os.close(fd)
    manifest=[]
    try:
        with zipfile.ZipFile(stage,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
            for business,quality,slug,document in prepared:
                prefix=slug+'/'
                z.writestr(prefix+'index.html',document)
                fields={
                    'Business Name':business['name'], 'Industry':business['industry'],
                    'Phone':business['phone'], 'Email':business['email'],
                    'Address':business['address'], 'Google Maps Link':business['maps_url'],
                    'Website Status':business['website_status']}
                buffer=io.StringIO()
                writer=csv.writer(buffer)
                for k,v in fields.items():writer.writerow((k,v))
                z.writestr(prefix+'business-details.csv',buffer.getvalue().encode('utf-8-sig'))
                public_quality={k:v for k,v in quality.items() if k not in ('output_path','business_id')}
                z.writestr(prefix+'quality-report.json',json.dumps(public_quality,ensure_ascii=False,indent=2))
                manifest.append({'business':business['name'],'folder':slug,
                                 'html_sha256':quality['actual_sha256'],'quality_status':quality['status']})
            z.writestr('delivery-manifest.json',json.dumps({'created_at':now(),'websites':manifest},ensure_ascii=False,indent=2))
            z.writestr('README.txt','TAZORIXX client website delivery\nEach folder includes a standalone index.html, source business details, and a quality report.\nOpen index.html in a browser. Confirm client approval before publishing.\n')
        with zipfile.ZipFile(stage) as verify:
            if verify.testzip() is not None:
                raise RuntimeError('ZIP integrity validation failed')
        # Atomic exclusive link means a concurrent file cannot be overwritten.
        if os.name == 'nt':
            os.rename(stage,zip_path)
        else:
            os.link(stage,zip_path)
    finally:
        Path(stage).unlink(missing_ok=True)
    return zip_path


# MVP 15 — zero-review automatic file preparation. This does not send messages.
def _business_html_filename(name, business_id, occupied):
    """Readable client filenames, with stable identity suffix only on collisions."""
    stem=re.sub(r'[^a-zA-Z0-9 _-]+', ' ', str(name)).strip()
    stem=re.sub(r'\s+', ' ', stem).strip(' .')[:90] or 'Business'
    proposed=stem+'.html'
    if proposed.casefold() in occupied:
        proposed=stem+'-'+hashlib.sha256(business_id.encode()).hexdigest()[:9]+'.html'
    occupied.add(proposed.casefold())
    return proposed


def auto_prepare_websites(conn, template_path, output_dir, statuses=('missing', 'social_only')):
    """Prepare validated standalone business-named HTML and a machine-readable outbound queue.

    Every run scans imported businesses, independently resolves industry profiles and
    exports only missing/social-only websites. It does not modify client approvals or
    transmit unsolicited messages. A file is queued only if static QC passes and a
    contact phone or email exists. Failures and skipped leads are logged.
    """
    if any(x not in ('missing', 'social_only') for x in statuses):
        raise ValueError('Auto mode only supports missing or social-only website records')
    root=Path(output_dir); root.mkdir(parents=True,exist_ok=True)
    html_dir=root/'Business_HTML'; html_dir.mkdir(exist_ok=True)
    internal_dir=root/'_validated_exports'; internal_dir.mkdir(exist_ok=True)
    selected=conn.execute('SELECT id,name,industry,website_status,phone,email,whatsapp FROM businesses ORDER BY lower(name),id').fetchall()
    occupied=set(); results=[]; queued=[]
    for b in selected:
        if b['website_status'] not in statuses:
            results.append({'business':b['name'],'status':'skipped','reason':'Existing website or unverified website status'})
            continue
        filename=_business_html_filename(b['name'],b['id'],occupied)
        try:
            # Always update generated output to reflect the latest Excel source and settings.
            source=export_business(conn,b['id'],template_path,internal_dir,overwrite=True)
            quality=website_quality_report(conn,b['id'])
            if quality['errors']:
                raise ValueError('HTML quality failed: '+ '; '.join(issue['detail'] for issue in quality['errors']))
            html_bytes=source.read_bytes()
            digest=hashlib.sha256(html_bytes).hexdigest()
            if digest != quality['actual_sha256']:
                raise ValueError('HTML changed since validation')
            target=html_dir/filename
            fd,staged=tempfile.mkstemp(prefix='.tazorixx-ready-',suffix='.tmp',dir=html_dir)
            try:
                with os.fdopen(fd,'wb') as stream:
                    stream.write(html_bytes);stream.flush();os.fsync(stream.fileno())
                os.replace(staged,target)
            finally:Path(staged).unlink(missing_ok=True)
            contact=b['whatsapp'] or b['phone'] or b['email']
            item={'business_id':b['id'],'business_name':b['name'],'industry':b['industry'],
                  'file_name':filename,'html_path':str(target.resolve()),'sha256':digest,
                  'contact_phone':b['phone'] or '', 'contact_whatsapp':b['whatsapp'] or '',
                  'contact_email':b['email'] or '', 'quality_status':quality['status'],
                  'action':'pending_external_delivery' if contact else 'contact_missing',
                  'generated_utc':now()}
            if contact:queued.append(item)
            results.append({'business':b['name'],'status':'ready' if contact else 'contact_missing',
                            'file_name':filename,'quality':quality['status']})
        except Exception as exc:
            results.append({'business':b['name'],'status':'failed','reason':str(exc)})
    manifest={'generated_utc':now(),'automatically_sent':False,
              'note':'No WhatsApp/email transport configured. Queue records are NOT sent messages.',
              'websites':queued,'results':results}
    manifest_path=root/'send_queue.json'
    fd,staged=tempfile.mkstemp(prefix='.tazorixx-queue-',suffix='.tmp',dir=root)
    try:
        with os.fdopen(fd,'w',encoding='utf8') as stream:
            json.dump(manifest,stream,ensure_ascii=False,indent=2);stream.flush();os.fsync(stream.fileno())
        os.replace(staged,manifest_path)
    finally:Path(staged).unlink(missing_ok=True)
    return {'output_dir':str(root),'html_dir':str(html_dir),'send_queue':str(manifest_path),
            'ready':sum(r['status']=='ready' for r in results),
            'contact_missing':sum(r['status']=='contact_missing' for r in results),
            'failed':sum(r['status']=='failed' for r in results),
            'skipped':sum(r['status']=='skipped' for r in results),'results':results}

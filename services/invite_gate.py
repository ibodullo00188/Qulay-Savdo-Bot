"""Admin-configured admission for free posts, with unique referral accounting."""
import config
from database.db import get_db
from services.transactions import one, transaction
import database.repo as repo


class InviteRequired(ValueError):
    pass


async def migrate(db):
    await db.executescript('''
        CREATE TABLE IF NOT EXISTS invite_grants(user_id INTEGER PRIMARY KEY, required INTEGER NOT NULL, granted_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS invite_spending(member_id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL,
            kind TEXT NOT NULL, item_id INTEGER NOT NULL, spent_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS invite_spending_owner ON invite_spending(owner_id);
    ''')
    for key,value in [('invite_gate_enabled','0'),('invite_gate_count','0'),('invite_gate_mode','')]:
        await db.execute('INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)',(key,value))


async def settings(db=None):
    db=db or await get_db()
    values={}
    for key in ('invite_gate_enabled','invite_gate_count','invite_gate_mode','paid_enabled'):
        row=await one(db,'SELECT value FROM settings WHERE key=?',(key,))
        values[key]=row['value'] if row else ''
    return dict(enabled=values['invite_gate_enabled']=='1', count=int(values['invite_gate_count'] or 0),
                mode=values['invite_gate_mode'], paid=values['paid_enabled']!='0')


async def progress(db,uid,opts):
    used="AND NOT EXISTS (SELECT 1 FROM invite_spending s WHERE s.member_id=m.user_id)" if opts['mode']=='per_post' else ''
    row=await one(db,f'SELECT COUNT(*) n FROM channel_members m WHERE m.referrer_id=? AND m.present=1 {used}',(uid,))
    grant=await one(db,'SELECT user_id FROM invite_grants WHERE user_id=?',(uid,))
    return row['n'], bool(grant)


async def require(db,uid,kind=None,item_id=None,consume=False):
    opts=await settings(db)
    if not opts['enabled'] or opts['paid']:
        return
    if opts['count']<1 or opts['mode'] not in ('once','per_post'):
        raise InviteRequired('Taklif sozlamalari to‘liq emas. Admin bilan bog‘laning.')
    count,granted=await progress(db,uid,opts)
    if opts['mode']=='once' and granted:return
    if count<opts['count']:
        raise InviteRequired(f'Yana {opts["count"]-count} ta do‘stni kanalga taklif qiling.')
    if opts['mode']=='once':
        await db.execute('INSERT OR IGNORE INTO invite_grants VALUES (?,?,?)',(uid,opts['count'],repo._now()))
    elif consume:
        cur=await db.execute('''SELECT m.user_id FROM channel_members m WHERE m.referrer_id=? AND m.present=1
            AND NOT EXISTS (SELECT 1 FROM invite_spending s WHERE s.member_id=m.user_id)
            ORDER BY m.user_id LIMIT ?''',(uid,opts['count']))
        for row in await cur.fetchall():
            await db.execute('INSERT INTO invite_spending VALUES (?,?,?,?,?)',(row['user_id'],uid,kind,item_id,repo._now()))


async def member_arrived(db,uid):
    member=await one(db,'SELECT referrer_id FROM channel_members WHERE user_id=? AND present=1',(uid,))
    if not member or not member['referrer_id']:return
    opts=await settings(db)
    if not opts['enabled'] or opts['paid'] or opts['mode']!='once':return
    count,granted=await progress(db,member['referrer_id'],opts)
    if not granted and opts['count']>0 and count>=opts['count']:
        await require(db,member['referrer_id'])
        from services.applications import enqueue
        await enqueue(db,member['referrer_id'],'✅ Do‘st taklif qilish sharti bajarildi! Qoralamangizga qaytib, “Tekshirish va davom etish”ni bosing. Bir martalik shart keyingi postlarda takrorlanmaydi.')


async def change(admin_id,key,value):
    if not await repo.is_admin(admin_id):raise ValueError('Ruxsat yo‘q.')
    if key=='count':
        if not str(value).isascii() or not str(value).isdigit() or not 1<=int(value)<=10000:
            raise ValueError('1–10000 oralig‘ida butun son kiriting.')
        value=str(int(value))
    elif key=='mode':
        if value not in ('once','per_post'):raise ValueError('Noto‘g‘ri rejim.')
    elif key=='enabled':
        if value not in ('0','1'):raise ValueError('Noto‘g‘ri holat.')
    else:raise ValueError('Noto‘g‘ri sozlama.')
    async with transaction() as db:
        opts=await settings(db)
        if key=='enabled' and value=='1' and (opts['count']<1 or opts['mode'] not in ('once','per_post')):
            raise ValueError('Avval do‘stlar sonini va talab rejimini tanlang.')
        await db.execute('UPDATE settings SET value=?,updated_by=?,updated_at=? WHERE key=?',
                         (value,admin_id,repo._now(),'invite_gate_'+key))
        await db.execute("INSERT INTO admin_actions(admin_id,action_type,description) VALUES (?,'INVITE_GATE_SETTING',?)",(admin_id,key+'='+value))


async def invite_link(bot,uid):
    db=await get_db()
    stored=await one(db,'SELECT link FROM referral_links WHERE owner_id=?',(uid,))
    if stored:return stored['link']
    if not config.CHANNEL_ID:raise ValueError('Kanal sozlanmagan. Admin bilan bog‘laning.')
    invite=await bot.create_chat_invite_link(config.CHANNEL_ID,name=f'ref-{uid}')
    await db.execute('INSERT INTO referral_links(link,owner_id) VALUES (?,?)',(invite.invite_link,uid))
    return invite.invite_link

from __future__ import annotations
from typing import List, Dict, Literal, Any
from fastapi import FastAPI, HTTPException, Depends, Query, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from datetime import datetime, timedelta
import itertools, json, os, hashlib, math

from dotenv import load_dotenv
load_dotenv()  # carga .env si existe (desarrollo local; en Railway se usan env vars del panel)

ADMIN_PIN = os.environ.get("ADMIN_PIN", "1234")

from sqlalchemy import (
    create_engine, Column, Integer, String, Float, DateTime,
    ForeignKey, UniqueConstraint, Text, Boolean, func, event, extract
)
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./teams.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ---- MODELS ----
class PlayerIdSequence(Base):
    """Tracks max ever-used player ID to prevent ID recycling on SQLite."""
    __tablename__ = "player_id_sequence"
    id = Column(Integer, primary_key=True, default=1)
    max_id = Column(Integer, default=0)

class Player(Base):
    __tablename__ = "players"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    is_goalkeeper = Column(Boolean, default=False)
    regularity = Column(Float, default=0.7)
    shot_min = Column(Float, default=4.0);  shot_max = Column(Float, default=6.0)
    passing_min = Column(Float, default=4.0); passing_max = Column(Float, default=6.0)
    defense_min = Column(Float, default=4.0); defense_max = Column(Float, default=6.0)
    vision_min = Column(Float, default=4.0);  vision_max = Column(Float, default=6.0)
    stamina_min = Column(Float, default=4.0); stamina_max = Column(Float, default=6.0)
    speed_min = Column(Float, default=4.0);   speed_max = Column(Float, default=6.0)
    password_hash = Column(String, nullable=True)
    last_login = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Preference(Base):
    __tablename__ = "preferences"
    id = Column(Integer, primary_key=True)
    src_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    dst_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    weight = Column(Integer, default=0)
    __table_args__ = (UniqueConstraint("src_id", "dst_id", name="uq_pref_pair"),)

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    is_admin = Column(Boolean, default=False)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class Opinion(Base):
    __tablename__ = "opinions"
    id = Column(Integer, primary_key=True)
    actor_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    target_player_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    shot_min = Column(Float, nullable=False);   shot_max = Column(Float, nullable=False)
    passing_min = Column(Float, nullable=False); passing_max = Column(Float, nullable=False)
    defense_min = Column(Float, nullable=False); defense_max = Column(Float, nullable=False)
    vision_min = Column(Float, nullable=False);  vision_max = Column(Float, nullable=False)
    stamina_min = Column(Float, nullable=False); stamina_max = Column(Float, nullable=False)
    speed_min = Column(Float, nullable=False);   speed_max = Column(Float, nullable=False)
    is_goalkeeper_self = Column(Boolean, default=None)
    created_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("actor_user_id", "target_player_id", name="uq_opinion_actor_target"),)

class Match(Base):
    __tablename__ = "matches"
    id = Column(Integer, primary_key=True)
    team_a = Column(Text, nullable=False)
    team_b = Column(Text, nullable=False)
    scheduled_at = Column(DateTime, nullable=True)
    is_recorded = Column(Boolean, default=False)
    winner_team = Column(String, nullable=True)
    goal_diff = Column(Integer, nullable=True)
    score_a = Column(Integer, default=0)
    score_b = Column(Integer, default=0)
    played_at = Column(DateTime, nullable=True)
    rank_winners = Column(Text, nullable=True)
    rank_losers = Column(Text, nullable=True)
    trends_snapshot = Column(Text, nullable=True)

Base.metadata.create_all(bind=engine)

# ---- MIGRATIONS: agregar columnas nuevas a tablas existentes ----
def _run_migrations():
    for sql in [
        "ALTER TABLE players ADD COLUMN password_hash VARCHAR",
        "ALTER TABLE players ADD COLUMN last_login TIMESTAMP",
    ]:
        with engine.connect() as conn:
            try:
                conn.execute(__import__('sqlalchemy').text(sql))
                conn.commit()
            except Exception:
                conn.rollback()
_run_migrations()

# ---- MIGRATE: visión como valor único ----
def _migrate_vision_single():
    """Normaliza vision_min = vision_max = promedio para jugadores y opiniones existentes."""
    sql_avg = __import__('sqlalchemy').text
    for table in ("players", "opinions"):
        with engine.connect() as conn:
            try:
                conn.execute(sql_avg(
                    f"UPDATE {table} "
                    f"SET vision_min = (vision_min + vision_max) / 2.0, "
                    f"    vision_max = (vision_min + vision_max) / 2.0 "
                    f"WHERE vision_min != vision_max"
                ))
                conn.commit()
            except Exception:
                conn.rollback()
_migrate_vision_single()

# ---- INIT SEQUENCE ----
# Asegura que la secuencia de IDs de jugadores registre el máximo actual
def _init_player_sequence():
    db = SessionLocal()
    try:
        seq = db.query(PlayerIdSequence).filter(PlayerIdSequence.id==1).first()
        current_max = db.query(func.max(Player.id)).scalar() or 0
        if not seq:
            db.add(PlayerIdSequence(id=1, max_id=current_max)); db.commit()
        elif seq.max_id < current_max:
            seq.max_id = current_max; db.commit()
    finally:
        db.close()
_init_player_sequence()

# ---- WEIGHTS ----
FIELD_WEIGHTS = {"shot":0.9,"passing":1.0,"defense":0.9,"vision":2.5,"stamina":0.7,"speed":0.6}
GK_WEIGHTS    = FIELD_WEIGHTS  # En F5 el arquero juega como un jugador más

def compute_overall(p: Player) -> float:
    w = GK_WEIGHTS if p.is_goalkeeper else FIELD_WEIGHTS
    def avg(a,b): return (a+b)/2.0
    return (avg(p.shot_min,p.shot_max)*w["shot"] + avg(p.passing_min,p.passing_max)*w["passing"] +
            avg(p.defense_min,p.defense_max)*w["defense"] + avg(p.vision_min,p.vision_max)*w["vision"] +
            avg(p.stamina_min,p.stamina_max)*w["stamina"] + avg(p.speed_min,p.speed_max)*w["speed"])

def overall_from_vals(vals, is_gk):
    w = GK_WEIGHTS if is_gk else FIELD_WEIGHTS
    return sum(vals[k]*w[k] for k in w)

def admin_attr_vals(p: Player):
    return {k: (getattr(p,f"{k}_min")+getattr(p,f"{k}_max"))/2 for k in ["shot","passing","defense","vision","stamina","speed"]}

def combined_attr_minmax(db, pid, p: Player):
    """Returns (avg_min, avg_max) dicts from all opinions. Falls back to admin raw min/max if none."""
    ops = db.query(Opinion).filter(Opinion.target_player_id==pid).all()
    attrs = ["shot","passing","defense","vision","stamina","speed"]
    if not ops:
        return ({a: getattr(p, f"{a}_min") for a in attrs},
                {a: getattr(p, f"{a}_max") for a in attrs})
    n = len(ops)
    return ({a: sum(getattr(o, f"{a}_min") for o in ops)/n for a in attrs},
            {a: sum(getattr(o, f"{a}_max") for o in ops)/n for a in attrs})

def combined_attr_vals(db, pid, admin_vals):
    """Promedia TODAS las opiniones por igual (incluyendo la del admin si la cargó).
    Si no hay ninguna opinión registrada, devuelve los valores base del jugador."""
    ops = db.query(Opinion).filter(Opinion.target_player_id==pid).all()
    if not ops: return dict(admin_vals)
    attrs = list(admin_vals.keys())
    combined = {}
    for attr in attrs:
        vals = [(getattr(o,f"{attr}_min")+getattr(o,f"{attr}_max"))/2 for o in ops]
        combined[attr] = sum(vals)/len(vals)
    return combined

# ---- SCHEMAS ----
class PlayerIn(BaseModel):
    name: str
    is_goalkeeper: bool = False
    regularity: float = Field(0.7, ge=0, le=1)
    shot_min: float = Field(4,ge=1,le=10); shot_max: float = Field(6,ge=1,le=10)
    passing_min: float = Field(4,ge=1,le=10); passing_max: float = Field(6,ge=1,le=10)
    defense_min: float = Field(4,ge=1,le=10); defense_max: float = Field(6,ge=1,le=10)
    vision_min: float = Field(4,ge=1,le=10);  vision_max: float = Field(6,ge=1,le=10)
    stamina_min: float = Field(4,ge=1,le=10); stamina_max: float = Field(6,ge=1,le=10)
    speed_min: float = Field(4,ge=1,le=10);   speed_max: float = Field(6,ge=1,le=10)

class PlayerOut(PlayerIn):
    id: int; overall_expected: float
    password_hash: str | None = None
    last_login: datetime | None = None
    created_at: datetime | None = None
    class Config: orm_mode = True

class PlayerWithOpinionsOut(BaseModel):
    id: int; name: str; is_goalkeeper: bool; regularity: float
    admin: Dict[str,float]; combined: Dict[str,float]
    overall_admin: float; overall_combined: float
    attr_min: Dict[str,float]; attr_max: Dict[str,float]
    combined_min: Dict[str,float]; combined_max: Dict[str,float]
    class Config: orm_mode = True

class PreferenceIn(BaseModel):
    src_id: int; dst_id: int; weight: int = Field(0,ge=-2,le=2)

class PreferenceOut(PreferenceIn):
    id: int
    class Config: orm_mode = True

class UserIn(BaseModel):
    name: str; is_admin: bool = False; player_id: int | None = None

class UserOut(UserIn):
    id: int
    class Config: orm_mode = True

class OpinionIn(BaseModel):
    actor_user_id: int; target_player_id: int
    shot_min: float; shot_max: float
    passing_min: float; passing_max: float
    defense_min: float; defense_max: float
    vision_min: float;  vision_max: float
    stamina_min: float; stamina_max: float
    speed_min: float;   speed_max: float
    is_goalkeeper_self: bool | None = None

class OpinionOut(OpinionIn):
    id: int; created_at: datetime
    class Config: orm_mode = True

class MatchScheduleIn(BaseModel):
    team_a: List[int]; team_b: List[int]; scheduled_at: datetime | None = None

class MatchOut(BaseModel):
    id: int; team_a: List[int]; team_b: List[int]
    scheduled_at: datetime | None; is_recorded: bool
    winner_team: Literal["A","B","D",None] = None
    goal_diff: int | None
    played_at: datetime | None
    rank_winners: List[int] | None; rank_losers: List[int] | None
    trends_snapshot: Dict[int,Dict[str,Any]] | None = None

class MatchResultIn(BaseModel):
    goal_diff: int = Field(..., ge=0); winner_team: Literal["A","B","D"]
    rank_winners: List[int] = Field(default_factory=list)
    rank_losers: List[int] = Field(default_factory=list)
    played_at: datetime | None = None

class TeamGenRequest(BaseModel):
    player_ids: List[int]; lambda_syn: float = 10.0; use_synergy: bool = True

class TeamGenResponse(BaseModel):
    team_a: List[int]; team_b: List[int]
    score_diff: float; syn_sum: float; skill_sum_a: float; skill_sum_b: float
    option_num: int = 1

class PredictRequest(BaseModel):
    team_a: List[int]; team_b: List[int]; lambda_syn: float = 10.0; use_synergy: bool = True

class PredictResponse(BaseModel):
    prob_a: float; prob_b: float; score_a: float; score_b: float
    syn_a: float; syn_b: float; skill_a: float; skill_b: float

class PlayerTrendOut(BaseModel):
    player_id: int; trend: Literal["up2","up1","flat","down1","down2"]; score: int

class PlayerSeasonStat(BaseModel):
    player_id: int; name: str; gp: int; wins: int; losses: int; draws: int
    perf_points: int; win_rate: float

class MatchHistoryEntry(BaseModel):
    match_id: int; played_at: datetime; winner_team: str
    goal_diff: int | None; my_team: str; result: str
    perf_score: int; rank_pos: int | None

class ChemistryOut(BaseModel):
    best_partner_id: int | None; best_partner_name: str | None; best_partner_win_rate: float | None
    worst_rival_id: int | None; worst_rival_name: str | None; worst_rival_win_rate: float | None

# ---- APP ----
app = FastAPI(title="Futbol5 API", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["*"], allow_headers=["*"])

# Serve frontend if it exists
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.isdir(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

    @app.get("/")
    def serve_index():
        return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))

# ---- HELPERS ----
def get_session():
    db = SessionLocal()
    try: yield db
    finally: db.close()

def csv_join(ids): return ",".join(str(i) for i in ids)
def csv_split(s): return [int(x) for x in s.split(",") if x] if s else None

def clamp(v): return float(max(1.0, min(10.0, v)))

def next_thursday():
    dt = datetime.utcnow()
    days_ahead = (3 - dt.weekday()) % 7 or 7
    return (dt + timedelta(days=days_ahead)).replace(hour=21,minute=0,second=0,microsecond=0)

def _perf_score(pid, m):
    rw = csv_split(m.rank_winners) or []
    rl = csv_split(m.rank_losers) or []

    # Rankeado como ganador
    if pid in rw:
        pos = rw.index(pid) + 1
        return 2 if pos <= 2 else 1

    # Rankeado como perdedor
    if pid in rl:
        pos = rl.index(pid) + 1
        return 0 if pos == 1 else -1

    # No rankeado: distinguir si ganó o perdió
    if m.winner_team in ("A", "B"):
        win_ids  = csv_split(m.team_a) if m.winner_team == "A" else csv_split(m.team_b)
        lose_ids = csv_split(m.team_b) if m.winner_team == "A" else csv_split(m.team_a)
        if pid in (win_ids or []):
            return 0   # Ganó pero sin destacarse
        if pid in (lose_ids or []):
            return -2  # Perdió y no fue destacado (los 2 peores)

    return 0  # Empate o no jugó

def _in_match(pid, m):
    return pid in (csv_split(m.team_a) or []) + (csv_split(m.team_b) or [])

def _trend(total):
    if total >= 5: return "up2"
    if total >= 3: return "up1"
    if total >= -2: return "flat"
    if total >= -5: return "down1"
    return "down2"

STREAK_FACTORS = {"up2": 0.95, "up1": 0.70, "flat": 0.50, "down1": 0.30, "down2": 0.05}

def compute_overall_with_trend(p: Player, trend: str) -> float:
    factor = STREAK_FACTORS.get(trend, 0.50)
    w = GK_WEIGHTS if p.is_goalkeeper else FIELD_WEIGHTS
    total = 0.0
    for attr in ["shot", "passing", "defense", "vision", "stamina", "speed"]:
        val = getattr(p, f"{attr}_min") + factor * (getattr(p, f"{attr}_max") - getattr(p, f"{attr}_min"))
        total += val * w[attr]
    return total

def compute_combined_with_trend(p: Player, trend: str, db) -> float:
    """OVR basado 100% en opiniones. El admin no aporta ningún valor implícito:
    si existen opiniones, el rango (min/max) y el promedio se derivan de ellas;
    los atributos almacenados en el jugador solo sirven de fallback neutral cuando
    no hay ninguna opinión registrada."""
    admin_vals = admin_attr_vals(p)
    combined = combined_attr_vals(db, p.id, admin_vals)
    op_min, op_max = combined_attr_minmax(db, p.id, p)   # rango de opiniones (o stored como fallback)
    factor = STREAK_FACTORS.get(trend, 0.50)
    w = GK_WEIGHTS if p.is_goalkeeper else FIELD_WEIGHTS
    total = 0.0
    for attr in ["shot", "passing", "defense", "vision", "stamina", "speed"]:
        r_min = op_min[attr]
        r_max = op_max[attr]
        combined_val = combined.get(attr, (r_min + r_max) / 2)
        val = r_min + factor * (r_max - r_min)
        total += ((combined_val + val) / 2) * w[attr]
    return total

def trends_snapshot(db, pids, lookback=3):
    matches = db.query(Match).filter(Match.is_recorded==True).order_by(Match.played_at.desc()).all()
    out = {}
    for pid in pids:
        total, count = 0, 0
        for m in matches:
            if _in_match(pid, m):
                total += _perf_score(pid, m); count += 1
                if count >= lookback: break
        out[pid] = {"trend": _trend(total), "score": total}
    return out

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def pref_weight(db, i, j):
    p = db.query(Preference).filter(Preference.src_id==i, Preference.dst_id==j).first()
    return p.weight if p else 0

def team_score(db, team, lambda_syn, ovrs):
    skill = sum(ovrs[i] for i in team)
    syn = sum((pref_weight(db,a,b)+pref_weight(db,b,a))/2.0 for a,b in itertools.combinations(team,2))
    return skill + lambda_syn*syn, syn, skill

def to_match_out(m):
    trends = None
    if m.trends_snapshot:
        try: trends = {int(k):v for k,v in json.loads(m.trends_snapshot).items()}
        except Exception: pass
    return MatchOut(
        id=m.id, team_a=csv_split(m.team_a) or [], team_b=csv_split(m.team_b) or [],
        scheduled_at=m.scheduled_at, is_recorded=bool(m.is_recorded),
        winner_team=m.winner_team if m.winner_team in ("A","B","D") else None,
        goal_diff=m.goal_diff, played_at=m.played_at,
        rank_winners=csv_split(m.rank_winners), rank_losers=csv_split(m.rank_losers),
        trends_snapshot=trends,
    )

def player_out(p):
    return PlayerOut(id=p.id, name=p.name, is_goalkeeper=p.is_goalkeeper, regularity=p.regularity,
        shot_min=p.shot_min, shot_max=p.shot_max, passing_min=p.passing_min, passing_max=p.passing_max,
        defense_min=p.defense_min, defense_max=p.defense_max, vision_min=p.vision_min, vision_max=p.vision_max,
        stamina_min=p.stamina_min, stamina_max=p.stamina_max, speed_min=p.speed_min, speed_max=p.speed_max,
        overall_expected=compute_overall(p))

# ---- ROUTES: PLAYERS ----
@app.get("/players", response_model=List[PlayerOut])
def get_players(db=Depends(get_session)):
    return [player_out(p) for p in db.query(Player).order_by(Player.name).all()]

@app.get("/players/with_opinions", response_model=List[PlayerWithOpinionsOut])
def get_players_with_opinions(db=Depends(get_session)):
    out = []
    attrs = ["shot","passing","defense","vision","stamina","speed"]
    for p in db.query(Player).order_by(Player.name).all():
        av = admin_attr_vals(p); cv = combined_attr_vals(db, p.id, av)
        c_min, c_max = combined_attr_minmax(db, p.id, p)
        a_min = {a: getattr(p, f"{a}_min") for a in attrs}
        a_max = {a: getattr(p, f"{a}_max") for a in attrs}
        out.append(PlayerWithOpinionsOut(id=p.id, name=p.name, is_goalkeeper=p.is_goalkeeper,
            regularity=p.regularity, admin=av, combined=cv,
            overall_admin=overall_from_vals(av, p.is_goalkeeper),
            overall_combined=overall_from_vals(cv, p.is_goalkeeper),
            attr_min=a_min, attr_max=a_max, combined_min=c_min, combined_max=c_max))
    return out

@app.post("/players", response_model=PlayerOut)
def create_player(p: PlayerIn, db=Depends(get_session)):
    if db.query(Player).filter(Player.name==p.name).first():
        raise HTTPException(400, "Ya existe un jugador con ese nombre")
    # Garantizar PK única que nunca se reutiliza, incluso luego de borrar jugadores
    seq = db.query(PlayerIdSequence).filter(PlayerIdSequence.id==1).first()
    if not seq:
        seq = PlayerIdSequence(id=1, max_id=0); db.add(seq); db.flush()
    current_max = db.query(func.max(Player.id)).scalar() or 0
    new_id = max(seq.max_id, current_max) + 1
    seq.max_id = new_id
    pl = Player(id=new_id, **p.dict()); db.add(pl); db.commit(); db.refresh(pl)
    return player_out(pl)

@app.put("/players/{pid}", response_model=PlayerOut)
def update_player(pid: int, p: PlayerIn, db=Depends(get_session)):
    pl = db.query(Player).get(pid)
    if not pl: raise HTTPException(404, "Jugador no encontrado")
    for k,v in p.dict().items(): setattr(pl, k, v)
    db.commit(); db.refresh(pl); return player_out(pl)

@app.delete("/players/{pid}")
def delete_player(pid: int, db=Depends(get_session)):
    pl = db.query(Player).get(pid)
    if not pl: raise HTTPException(404, "Jugador no encontrado")
    # Limpiar todas las relaciones para evitar que un nuevo jugador con el mismo ID herede datos
    db.query(Preference).filter((Preference.src_id==pid) | (Preference.dst_id==pid)).delete(synchronize_session=False)
    db.query(Opinion).filter((Opinion.target_player_id==pid) | (Opinion.actor_user_id.in_(
        db.query(User.id).filter(User.player_id==pid).scalar_subquery()
    ))).delete(synchronize_session=False)
    db.query(User).filter(User.player_id==pid).update({User.player_id: None}, synchronize_session=False)
    db.delete(pl); db.commit(); return {"ok": True}

# ---- ROUTES: PREFERENCES ----
@app.get("/preferences", response_model=List[PreferenceOut])
def get_prefs(db=Depends(get_session)): return db.query(Preference).all()

@app.post("/preferences", response_model=PreferenceOut)
def upsert_pref(pref: PreferenceIn, db=Depends(get_session)):
    if pref.src_id == pref.dst_id: raise HTTPException(400, "No puedes referenciarte a ti mismo")
    ex = db.query(Preference).filter(Preference.src_id==pref.src_id, Preference.dst_id==pref.dst_id).first()
    if ex:
        ex.weight = pref.weight; db.commit(); db.refresh(ex); return ex
    np = Preference(**pref.dict()); db.add(np); db.commit(); db.refresh(np); return np

# ---- ROUTES: USERS ----
@app.get("/users", response_model=List[UserOut])
def list_users(db=Depends(get_session)): return db.query(User).order_by(User.created_at.desc()).all()

@app.post("/users", response_model=UserOut)
def create_user(u: UserIn, db=Depends(get_session)):
    user = User(**u.dict()); db.add(user); db.commit(); db.refresh(user); return user

@app.put("/users/{uid}", response_model=UserOut)
def update_user(uid: int, u: UserIn, db=Depends(get_session)):
    user = db.query(User).get(uid)
    if not user: raise HTTPException(404, "Usuario no encontrado")
    for k,v in u.dict().items(): setattr(user, k, v)
    db.commit(); db.refresh(user); return user

# ---- ROUTES: OPINIONS ----
@app.get("/opinions", response_model=List[OpinionOut])
def list_opinions(actor_user_id: int|None=None, target_player_id: int|None=None, db=Depends(get_session)):
    q = db.query(Opinion)
    if actor_user_id: q = q.filter(Opinion.actor_user_id==actor_user_id)
    if target_player_id: q = q.filter(Opinion.target_player_id==target_player_id)
    return q.order_by(Opinion.created_at.desc()).all()

@app.post("/opinions")
def add_opinion(op: OpinionIn, db=Depends(get_session)):
    if not db.query(User).get(op.actor_user_id): raise HTTPException(400, "actor_user_id inválido")
    pl = db.query(Player).get(op.target_player_id)
    if not pl: raise HTTPException(400, "target_player_id inválido")
    ex = db.query(Opinion).filter(Opinion.actor_user_id==op.actor_user_id, Opinion.target_player_id==op.target_player_id).first()
    if ex:
        for k,v in op.dict().items(): setattr(ex, k, v)
    else:
        db.add(Opinion(**op.dict()))
    db.commit()
    actor = db.query(User).get(op.actor_user_id)
    if op.is_goalkeeper_self is not None and actor and actor.player_id == op.target_player_id:
        pl.is_goalkeeper = bool(op.is_goalkeeper_self); db.commit()
    return {"ok": True}

# ---- ROUTES: TEAMS ----
@app.post("/generate_teams", response_model=List[TeamGenResponse])
def generate_teams(req: TeamGenRequest, db=Depends(get_session)):
    ids = req.player_ids
    if len(ids) != 10: raise HTTPException(400, "Debes enviar exactamente 10 jugadores")
    players = db.query(Player).filter(Player.id.in_(ids)).all()
    if len(players) != 10: raise HTTPException(400, "IDs inválidos")
    recent_matches = db.query(Match).filter(Match.is_recorded==True).order_by(Match.played_at.desc()).limit(4).all()
    def get_player_trend(pid):
        total = sum(_perf_score(pid, m) for m in recent_matches if _in_match(pid, m))
        return _trend(total)
    ovrs = {p.id: compute_combined_with_trend(p, get_player_trend(p.id), db) for p in players}
    ATTRS = ["shot", "passing", "defense", "vision", "stamina", "speed"]
    attr_vals = {}
    for p in players:
        admin_v = admin_attr_vals(p)
        attr_vals[p.id] = combined_attr_vals(db, p.id, admin_v)
    prefs = {}
    if req.use_synergy:
        rows = db.query(Preference).filter(
            Preference.src_id.in_(ids), Preference.dst_id.in_(ids)
        ).all()
        for r in rows:
            prefs[(r.src_id, r.dst_id)] = r.weight
    def syn_pair(a, b):
        return (prefs.get((a, b), 0) + prefs.get((b, a), 0)) / 2.0
    gk_ids = {p.id for p in players if p.is_goalkeeper}
    min_id = min(ids)
    top = []
    for comb in itertools.combinations(ids, 5):
        # Deduplicar: cada partición {ta,tb} aparece dos veces en combinations.
        # Solo procesamos la que contiene el ID más bajo en ta.
        if min_id not in set(comb): continue
        ta = list(comb); tb = [x for x in ids if x not in set(comb)]
        ta_set, tb_set = set(ta), set(tb)
        sk_a = sum(ovrs[i] for i in ta)
        sk_b = sum(ovrs[i] for i in tb)
        # GK balance: criterio primario absoluto
        gk_a = len(ta_set & gk_ids)
        gk_b = len(tb_set & gk_ids)
        if len(gk_ids) == 0:
            gk_penalty = 0
        elif gk_a == 0 or gk_b == 0:
            gk_penalty = 100
        else:
            gk_penalty = abs(gk_a - gk_b)
        # Sinergia por equipo (afecta el score total, no solo desempata)
        syn_a_val = 0.0; syn_b_val = 0.0
        if req.use_synergy:
            syn_a_val = sum(syn_pair(a, b) for a, b in itertools.combinations(ta, 2))
            syn_b_val = sum(syn_pair(a, b) for a, b in itertools.combinations(tb, 2))
        syn_total = syn_a_val + syn_b_val
        # Score total por equipo = habilidad + sinergia ponderada
        sc_a = sk_a + req.lambda_syn * syn_a_val
        sc_b = sk_b + req.lambda_syn * syn_b_val
        diff = abs(sc_a - sc_b)
        # Per-attribute balance penalty
        attr_diff = sum(
            abs(sum(attr_vals[i][a] for i in ta)/5 - sum(attr_vals[i][a] for i in tb)/5)
            for a in ATTRS
        )
        ATTR_BALANCE_W = 0.4
        # 1° GK balance, 2° diferencia total (skill+sinergia) + balance por atributo
        c = (gk_penalty, diff + ATTR_BALANCE_W * attr_diff, -(sk_a + sk_b))
        entry = (c, (ta, tb, round(diff, 2), round(syn_total, 2), round(sk_a, 2), round(sk_b, 2)))
        if len(top) < 3:
            top.append(entry); top.sort(key=lambda x: x[0])
        elif c < top[-1][0]:
            top[-1] = entry; top.sort(key=lambda x: x[0])
    if not top: raise HTTPException(400, "No se pudo generar equipos")
    return [
        TeamGenResponse(team_a=ta, team_b=tb, score_diff=diff, syn_sum=syn_sum,
                        skill_sum_a=sk_a, skill_sum_b=sk_b, option_num=i+1)
        for i, (_, (ta, tb, diff, syn_sum, sk_a, sk_b)) in enumerate(top)
    ]

@app.post("/predict_result", response_model=PredictResponse)
def predict_result(req: PredictRequest, db=Depends(get_session)):
    all_ids = list(set(req.team_a + req.team_b))
    players = {p.id: p for p in db.query(Player).filter(Player.id.in_(all_ids)).all()}
    recorded = db.query(Match).filter(Match.is_recorded==True).order_by(Match.played_at.desc()).all()
    def get_trend(pid):
        total, count = 0, 0
        for m in recorded:
            if _in_match(pid, m):
                total += _perf_score(pid, m); count += 1
                if count >= 3: break
        return _trend(total)
    ovrs = {pid: compute_combined_with_trend(players[pid], get_trend(pid), db) for pid in all_ids if pid in players}
    lsyn = req.lambda_syn if req.use_synergy else 0.0
    total_a, syn_a, skill_a = team_score(db, req.team_a, lsyn, ovrs)
    total_b, syn_b, skill_b = team_score(db, req.team_b, lsyn, ovrs)
    total = total_a + total_b
    prob_a = round(total_a / total * 100, 1) if total > 0 else 50.0
    prob_b = round(100 - prob_a, 1)
    return PredictResponse(prob_a=prob_a, prob_b=prob_b,
                           score_a=round(skill_a,2), score_b=round(skill_b,2),
                           syn_a=round(syn_a,2), syn_b=round(syn_b,2),
                           skill_a=round(skill_a,2), skill_b=round(skill_b,2))

# ---- ROUTES: MATCHES ----
@app.post("/matches", response_model=MatchOut)
def schedule_match(data: MatchScheduleIn, db=Depends(get_session)):
    if len(data.team_a)!=5 or len(data.team_b)!=5: raise HTTPException(400, "Cada equipo debe tener 5 jugadores")
    m = Match(team_a=csv_join(data.team_a), team_b=csv_join(data.team_b),
              scheduled_at=data.scheduled_at or next_thursday(), is_recorded=False)
    db.add(m); db.commit(); db.refresh(m); return to_match_out(m)

@app.get("/matches", response_model=List[MatchOut])
def list_matches(status: Literal["pending","played"]="pending", db=Depends(get_session)):
    q = db.query(Match)
    if status=="pending": q = q.filter(Match.is_recorded==False).order_by(Match.scheduled_at.desc())
    else: q = q.filter(Match.is_recorded==True).order_by(Match.played_at.desc())
    return [to_match_out(m) for m in q.all()]

@app.put("/matches/{mid}/result", response_model=MatchOut)
def record_result(mid: int, data: MatchResultIn, db=Depends(get_session)):
    m = db.query(Match).get(mid)
    if not m: raise HTTPException(404, "Partido no encontrado")
    if m.is_recorded: raise HTTPException(400, "Resultado ya cargado")
    m.winner_team=data.winner_team; m.goal_diff=data.goal_diff
    m.rank_winners=csv_join(data.rank_winners) if data.rank_winners else None
    m.rank_losers=csv_join(data.rank_losers) if data.rank_losers else None
    m.is_recorded=True; m.played_at=data.played_at or datetime.utcnow()
    all_ids = (csv_split(m.team_a) or []) + (csv_split(m.team_b) or [])
    m.trends_snapshot = json.dumps(trends_snapshot(db, all_ids, lookback=3))
    db.commit(); db.refresh(m); return to_match_out(m)

@app.delete("/matches/{mid}")
def delete_match(mid: int, db=Depends(get_session)):
    m = db.query(Match).get(mid)
    if not m: raise HTTPException(404, "Partido no encontrado")
    db.delete(m); db.commit(); return {"ok": True}

def check_admin(x_admin_pin: str = Header(None)):
    if x_admin_pin != ADMIN_PIN:
        raise HTTPException(401, "PIN incorrecto")

@app.post("/admin/verify")
def admin_verify(x_admin_pin: str = Header(None)):
    if x_admin_pin == ADMIN_PIN:
        return {"ok": True}
    raise HTTPException(401, "PIN incorrecto")

# ---- ROUTES: PLAYER PASSWORDS ----
@app.get("/players/{pid}/password_status")
def player_password_status(pid: int, db=Depends(get_session)):
    pl = db.query(Player).get(pid)
    if not pl: raise HTTPException(404, "Jugador no encontrado")
    return {"has_password": pl.password_hash is not None}

@app.post("/players/{pid}/set_password")
def set_player_password(pid: int, body: dict, db=Depends(get_session)):
    pl = db.query(Player).get(pid)
    if not pl: raise HTTPException(404, "Jugador no encontrado")
    pw = (body.get("password") or "").strip()
    if not pw: raise HTTPException(400, "Contraseña vacía")
    pl.password_hash = hash_password(pw)
    db.commit()
    return {"ok": True}

@app.post("/players/{pid}/verify_password")
def verify_player_password(pid: int, body: dict, db=Depends(get_session)):
    pl = db.query(Player).get(pid)
    if not pl: raise HTTPException(404, "Jugador no encontrado")
    if not pl.password_hash: return {"ok": True}
    if hash_password(body.get("password") or "") == pl.password_hash:
        return {"ok": True}
    raise HTTPException(401, "Contraseña incorrecta")

@app.post("/players/{pid}/reset_password")
def reset_player_password(pid: int, db=Depends(get_session), _=Depends(check_admin)):
    pl = db.query(Player).get(pid)
    if not pl: raise HTTPException(404, "Jugador no encontrado")
    pl.password_hash = None
    db.commit()
    return {"ok": True}

@app.post("/players/{pid}/ping")
def player_ping(pid: int, db=Depends(get_session)):
    pl = db.query(Player).get(pid)
    if not pl: raise HTTPException(404, "Jugador no encontrado")
    pl.last_login = datetime.utcnow()
    db.commit()
    return {"ok": True}

@app.get("/admin/participation")
def admin_participation(db=Depends(get_session)):
    players = db.query(Player).order_by(Player.name).all()
    opinions = db.query(Opinion).all()
    # actor_user_id is a user.id, not a player.id — build a lookup to translate
    user_to_player = {u.id: u.player_id for u in db.query(User).filter(User.player_id != None).all()}
    op_map = {}  # (actor_player_id, target_player_id) -> True
    for o in opinions:
        actor_pid = user_to_player.get(o.actor_user_id, o.actor_user_id)
        op_map[(actor_pid, o.target_player_id)] = True
    result = []
    for p in players:
        others = [x for x in players if x.id != p.id]
        self_eval = op_map.get((p.id, p.id), False)
        evals_given = sum(1 for x in others if op_map.get((p.id, x.id), False))
        evals_received = sum(1 for x in others if op_map.get((x.id, p.id), False))
        missing_evals = [x.name for x in others if not op_map.get((p.id, x.id), False)]
        result.append({
            "id": p.id, "name": p.name, "is_goalkeeper": p.is_goalkeeper,
            "self_eval": self_eval,
            "evals_given": evals_given, "total_to_give": len(others),
            "evals_received": evals_received,
            "missing_evals": missing_evals,
            "last_login": p.last_login.isoformat() if p.last_login else None,
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "has_password": p.password_hash is not None,
        })
    return result

@app.get("/players/trends", response_model=List[PlayerTrendOut])
def players_trends(lookback: int=4, db=Depends(get_session)):
    players = db.query(Player).all()
    matches = db.query(Match).filter(Match.is_recorded==True).order_by(Match.played_at.desc()).limit(lookback).all()
    out = []
    for p in players:
        total = sum(_perf_score(p.id, m) for m in matches if _in_match(p.id, m))
        out.append(PlayerTrendOut(player_id=p.id, trend=_trend(total), score=total))
    return out

@app.get("/stats/season", response_model=List[PlayerSeasonStat])
def season_stats(year: int | None = None, date_from: str | None = None, date_to: str | None = None, db=Depends(get_session)):
    q = db.query(Match).filter(Match.is_recorded==True)
    if year:
        q = q.filter(extract('year', Match.played_at) == year)
    if date_from:
        q = q.filter(Match.played_at >= datetime.fromisoformat(date_from))
    if date_to:
        q = q.filter(Match.played_at <= datetime.fromisoformat(date_to))
    matches = q.all()
    players = db.query(Player).all()
    out = []
    for p in players:
        gp = wins = losses = draws = perf = 0
        for m in matches:
            ta = csv_split(m.team_a) or []; tb = csv_split(m.team_b) or []
            if p.id in ta:
                gp += 1; perf += _perf_score(p.id, m)
                if m.winner_team=='A': wins += 1
                elif m.winner_team=='B': losses += 1
                else: draws += 1
            elif p.id in tb:
                gp += 1; perf += _perf_score(p.id, m)
                if m.winner_team=='B': wins += 1
                elif m.winner_team=='A': losses += 1
                else: draws += 1
        if gp > 0:
            out.append(PlayerSeasonStat(player_id=p.id, name=p.name, gp=gp, wins=wins,
                losses=losses, draws=draws, perf_points=perf,
                win_rate=round(wins/gp*100, 1)))
    return sorted(out, key=lambda x: (-x.perf_points, -x.win_rate))

@app.get("/players/{pid}/history", response_model=List[MatchHistoryEntry])
def player_history(pid: int, db=Depends(get_session)):
    matches = db.query(Match).filter(Match.is_recorded==True).order_by(Match.played_at.desc()).all()
    out = []
    for m in matches:
        ta = csv_split(m.team_a) or []; tb = csv_split(m.team_b) or []
        if pid in ta: my_team = 'A'
        elif pid in tb: my_team = 'B'
        else: continue
        result = 'D' if m.winner_team=='D' else ('W' if m.winner_team==my_team else 'L')
        rw = csv_split(m.rank_winners) or []; rl = csv_split(m.rank_losers) or []
        rank_pos = (rw.index(pid)+1) if pid in rw else ((rl.index(pid)+1) if pid in rl else None)
        out.append(MatchHistoryEntry(match_id=m.id, played_at=m.played_at, winner_team=m.winner_team,
            goal_diff=m.goal_diff, my_team=my_team, result=result,
            perf_score=_perf_score(pid, m), rank_pos=rank_pos))
    return out

@app.get("/players/{pid}/chemistry", response_model=ChemistryOut)
def player_chemistry(pid: int, db=Depends(get_session)):
    matches = db.query(Match).filter(Match.is_recorded==True).all()
    player_map = {p.id: p.name for p in db.query(Player).all()}
    partner_stats: Dict[int, list] = {}
    rival_stats: Dict[int, list] = {}
    for m in matches:
        ta = csv_split(m.team_a) or []; tb = csv_split(m.team_b) or []
        if pid in ta: my_team, opp_team, won = ta, tb, m.winner_team=='A'
        elif pid in tb: my_team, opp_team, won = tb, ta, m.winner_team=='B'
        else: continue
        for pid2 in my_team:
            if pid2 == pid: continue
            s = partner_stats.setdefault(pid2, [0,0]); s[0]+=1
            if won: s[1]+=1
        for pid2 in opp_team:
            s = rival_stats.setdefault(pid2, [0,0]); s[0]+=1
            if won: s[1]+=1
    def best(stats, highest):
        bst_id = bst_wr = None
        for pid2, (gp, w) in stats.items():
            if gp < 2: continue
            wr = w/gp
            if bst_wr is None or (wr > bst_wr if highest else wr < bst_wr): bst_wr = wr; bst_id = pid2
        return bst_id, round(bst_wr*100,1) if bst_wr is not None else None
    bp_id, bp_wr = best(partner_stats, True)
    br_id, br_wr = best(rival_stats, False)
    return ChemistryOut(
        best_partner_id=bp_id, best_partner_name=player_map.get(bp_id) if bp_id else None, best_partner_win_rate=bp_wr,
        worst_rival_id=br_id, worst_rival_name=player_map.get(br_id) if br_id else None, worst_rival_win_rate=br_wr)

@app.get("/players/{pid}/partners")
def player_partners(pid: int, min_games: int = 2, db=Depends(get_session)):
    matches = db.query(Match).filter(Match.is_recorded==True).all()
    player_map = {p.id: p.name for p in db.query(Player).all()}
    partner_stats: Dict[int, list] = {}  # pid2 -> [gp, wins]
    rival_stats: Dict[int, list] = {}
    for m in matches:
        ta = csv_split(m.team_a) or []; tb = csv_split(m.team_b) or []
        if pid in ta: my_team, opp_team, won = ta, tb, m.winner_team=='A'
        elif pid in tb: my_team, opp_team, won = tb, ta, m.winner_team=='B'
        else: continue
        draw = m.winner_team == 'D'
        for pid2 in my_team:
            if pid2 == pid: continue
            s = partner_stats.setdefault(pid2, [0,0,0]); s[0]+=1
            if won: s[1]+=1
            if draw: s[2]+=1
        for pid2 in opp_team:
            s = rival_stats.setdefault(pid2, [0,0,0]); s[0]+=1
            if won: s[1]+=1
            if draw: s[2]+=1

    def build_list(stats):
        out = []
        for pid2, (gp, wins, draws) in stats.items():
            if gp < min_games: continue
            out.append({
                "id": pid2, "name": player_map.get(pid2, f"#{pid2}"),
                "gp": gp, "wins": wins, "draws": draws, "losses": gp-wins-draws,
                "win_rate": round(wins/gp*100, 1)
            })
        return sorted(out, key=lambda x: (-x["win_rate"], -x["gp"]))

    return {
        "partners": build_list(partner_stats),
        "rivals": build_list(rival_stats),
    }

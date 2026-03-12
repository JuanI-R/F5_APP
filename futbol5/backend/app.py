from __future__ import annotations
from typing import List, Dict, Literal, Any
from fastapi import FastAPI, HTTPException, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from datetime import datetime, timedelta
import itertools, json, os

from sqlalchemy import (
    create_engine, Column, Integer, String, Float, DateTime,
    ForeignKey, UniqueConstraint, Text, Boolean, func, event
)
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./teams.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ---- MODELS ----
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

# ---- WEIGHTS ----
FIELD_WEIGHTS = {"shot":1.2,"passing":1.0,"defense":1.1,"vision":1.0,"stamina":0.9,"speed":0.9}
GK_WEIGHTS    = {"shot":0.4,"passing":0.9,"defense":1.4,"vision":1.0,"stamina":1.0,"speed":0.8}

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

def combined_attr_vals(db, pid, admin_vals):
    ops = db.query(Opinion).filter(Opinion.target_player_id==pid).all()
    if not ops: return dict(admin_vals)
    attrs = list(admin_vals.keys())
    combined = {}
    for attr in attrs:
        vals = [admin_vals[attr]] + [(getattr(o,f"{attr}_min")+getattr(o,f"{attr}_max"))/2 for o in ops]
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
    class Config: orm_mode = True

class PlayerWithOpinionsOut(BaseModel):
    id: int; name: str; is_goalkeeper: bool; regularity: float
    admin: Dict[str,float]; combined: Dict[str,float]
    overall_admin: float; overall_combined: float
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
    goal_diff: int | None; score_a: int; score_b: int
    played_at: datetime | None
    rank_winners: List[int] | None; rank_losers: List[int] | None
    trends_snapshot: Dict[int,Dict[str,Any]] | None = None

class MatchResultIn(BaseModel):
    score_a: int; score_b: int; winner_team: Literal["A","B","D"]
    rank_winners: List[int] = Field(default_factory=list)
    rank_losers: List[int] = Field(default_factory=list)

class TeamGenRequest(BaseModel):
    player_ids: List[int]; lambda_syn: float = 10.0

class TeamGenResponse(BaseModel):
    team_a: List[int]; team_b: List[int]
    score_diff: float; syn_sum: float; skill_sum_a: float; skill_sum_b: float

class PlayerTrendOut(BaseModel):
    player_id: int; trend: Literal["up","down","flat"]; score: int

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
    return (dt + timedelta(days=days_ahead)).replace(hour=20,minute=0,second=0,microsecond=0)

def _perf_score(pid, m):
    rw = csv_split(m.rank_winners) or []
    rl = csv_split(m.rank_losers) or []
    if pid in rw:
        pos = rw.index(pid)+1
        return 3 if pos==1 else (2 if pos==2 else 1)
    if pid in rl:
        return 2 if (rl.index(pid)+1)==1 else -1
    return 0

def _in_match(pid, m):
    return pid in (csv_split(m.team_a) or []) + (csv_split(m.team_b) or [])

def _trend(total): return "up" if total>=3 else ("down" if total<=-2 else "flat")

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
        except: pass
    return MatchOut(
        id=m.id, team_a=csv_split(m.team_a) or [], team_b=csv_split(m.team_b) or [],
        scheduled_at=m.scheduled_at, is_recorded=bool(m.is_recorded),
        winner_team=m.winner_team if m.winner_team in ("A","B","D") else None,
        goal_diff=m.goal_diff, score_a=m.score_a, score_b=m.score_b, played_at=m.played_at,
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
    for p in db.query(Player).order_by(Player.name).all():
        av = admin_attr_vals(p); cv = combined_attr_vals(db, p.id, av)
        out.append(PlayerWithOpinionsOut(id=p.id, name=p.name, is_goalkeeper=p.is_goalkeeper,
            regularity=p.regularity, admin=av, combined=cv,
            overall_admin=overall_from_vals(av, p.is_goalkeeper),
            overall_combined=overall_from_vals(cv, p.is_goalkeeper)))
    return out

@app.post("/players", response_model=PlayerOut)
def create_player(p: PlayerIn, db=Depends(get_session)):
    if db.query(Player).filter(Player.name==p.name).first():
        raise HTTPException(400, "Ya existe un jugador con ese nombre")
    pl = Player(**p.dict()); db.add(pl); db.commit(); db.refresh(pl)
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
@app.post("/generate_teams", response_model=TeamGenResponse)
def generate_teams(req: TeamGenRequest, db=Depends(get_session)):
    ids = req.player_ids
    if len(ids) != 10: raise HTTPException(400, "Debes enviar exactamente 10 jugadores")
    players = db.query(Player).filter(Player.id.in_(ids)).all()
    if len(players) != 10: raise HTTPException(400, "IDs inválidos")
    ovrs = {p.id: compute_overall(p) for p in players}
    gk_ids = {p.id for p in players if p.is_goalkeeper}
    best, best_c = None, None
    for comb in itertools.combinations(ids, 5):
        ta, tb = set(comb), set(ids)-set(comb)
        sa, syn_a, sk_a = team_score(db, list(ta), req.lambda_syn, ovrs)
        sb, syn_b, sk_b = team_score(db, list(tb), req.lambda_syn, ovrs)
        if len(gk_ids) >= 1:
            if not (ta & gk_ids): sa -= 25.0
            if not (tb & gk_ids): sb -= 25.0
        if len(gk_ids) >= 2:
            imb = abs(len(ta&gk_ids)-len(tb&gk_ids))
            if imb: sa -= 5.0*imb; sb -= 5.0*imb
        diff = abs(sa-sb); syn_total = syn_a+syn_b
        c = (diff, -syn_total, -(sk_a+sk_b))
        if best_c is None or c < best_c:
            best_c = c; best = (list(ta), list(tb), diff, syn_total, sk_a, sk_b)
    if not best: raise HTTPException(400, "No se pudo generar equipos")
    ta, tb, diff, syn_sum, sk_a, sk_b = best
    return TeamGenResponse(team_a=ta, team_b=tb, score_diff=diff, syn_sum=syn_sum, skill_sum_a=sk_a, skill_sum_b=sk_b)

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
    m.score_a=int(data.score_a); m.score_b=int(data.score_b)
    m.winner_team=data.winner_team; m.goal_diff=abs(m.score_a-m.score_b)
    m.rank_winners=csv_join(data.rank_winners) if data.rank_winners else None
    m.rank_losers=csv_join(data.rank_losers) if data.rank_losers else None
    m.is_recorded=True; m.played_at=datetime.utcnow()
    all_ids = (csv_split(m.team_a) or []) + (csv_split(m.team_b) or [])
    m.trends_snapshot = json.dumps(trends_snapshot(db, all_ids, lookback=3))
    db.commit(); db.refresh(m); return to_match_out(m)

@app.delete("/matches/{mid}")
def delete_match(mid: int, db=Depends(get_session)):
    m = db.query(Match).get(mid)
    if not m: raise HTTPException(404, "Partido no encontrado")
    db.delete(m); db.commit(); return {"ok": True}

@app.get("/players/trends", response_model=List[PlayerTrendOut])
def players_trends(lookback: int=3, db=Depends(get_session)):
    players = db.query(Player).all()
    matches = db.query(Match).filter(Match.is_recorded==True).order_by(Match.played_at.desc()).all()
    out = []
    for p in players:
        total, count = 0, 0
        for m in matches:
            if _in_match(p.id, m):
                total += _perf_score(p.id, m); count += 1
                if count >= lookback: break
        out.append(PlayerTrendOut(player_id=p.id, trend=_trend(total), score=total))
    return out

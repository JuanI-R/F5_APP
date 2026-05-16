import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["DATABASE_URL"] = "sqlite:///./test_modify_votes_temp.db"

from fastapi.testclient import TestClient
from app import app, Base, engine, SessionLocal, Player, Match, MatchVote
import pytest
from datetime import datetime, timedelta

@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)

@pytest.fixture
def client():
    return TestClient(app)

@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()

@pytest.fixture
def match_with_vote(db):
    """Creates 10 players, records a match, and submits one vote. Returns (match, team_a_ids, team_b_ids, voter_id)."""
    players = []
    for i in range(1, 11):
        p = Player(name=f"Player{i}", shot_min=5, shot_max=7, passing_min=5, passing_max=7,
                   defense_min=5, defense_max=7, vision_min=5, vision_max=7,
                   stamina_min=5, stamina_max=7, speed_min=5, speed_max=7)
        db.add(p)
    db.commit()
    players = db.query(Player).order_by(Player.id).all()
    team_a = [p.id for p in players[:5]]
    team_b = [p.id for p in players[5:]]

    m = Match(
        team_a=",".join(str(i) for i in team_a),
        team_b=",".join(str(i) for i in team_b),
        is_recorded=True,
        winner_team="A",
        goal_diff=2,
        played_at=datetime.utcnow() - timedelta(hours=1),
        voting_deadline=datetime.utcnow() - timedelta(days=1),
    )
    db.add(m)
    db.commit()
    db.refresh(m)

    voter = team_b[0]
    vote = MatchVote(
        match_id=m.id,
        voter_player_id=voter,
        target_team="A",
        rank_1=team_a[0], rank_2=team_a[1], rank_3=team_a[2],
        rank_4=team_a[3], rank_5=team_a[4],
        voted_by_admin=False,
        submitted_at=datetime.utcnow() - timedelta(hours=1),
    )
    db.add(vote)
    db.commit()

    return m, team_a, team_b, voter

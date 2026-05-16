# Admin Modify Votes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow admin to modify any player's existing ranking vote on the last recorded match, bypassing the voting deadline and duplicate-vote guards.

**Architecture:** New `PUT /matches/{mid}/votes/{voter_player_id}` endpoint (admin-only) updates the existing vote row and recomputes Borda rankings. Frontend adds an "✏️ Modificar voto" button visible only on the last recorded match, opening a modal with pre-populated current rankings.

**Tech Stack:** FastAPI + SQLAlchemy (backend), vanilla JS (frontend), pytest (tests)

---

## Files

| Action | Path |
|--------|------|
| Modify | `futbol5/backend/app.py` — new PUT endpoint after line 1111 |
| Create | `futbol5/backend/tests/conftest.py` — test fixtures |
| Create | `futbol5/backend/tests/test_modify_votes.py` — endpoint tests |
| Modify | `futbol5/frontend/index.html` — modal function + button |

---

### Task 1: Backend — PUT endpoint

**Files:**
- Modify: `futbol5/backend/app.py` (after the `submit_vote` endpoint, ~line 1111)
- Create: `futbol5/backend/tests/conftest.py`
- Create: `futbol5/backend/tests/test_modify_votes.py`

- [ ] **Step 1.1: Create test fixtures**

Create `futbol5/backend/tests/conftest.py`:

```python
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
        voting_deadline=datetime.utcnow() - timedelta(days=1),  # deadline already passed
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
```

- [ ] **Step 1.2: Write failing tests**

Create `futbol5/backend/tests/test_modify_votes.py`:

```python
from app import ADMIN_PIN

ADMIN_HEADERS = {"X-Admin-Pin": ADMIN_PIN}

def test_modify_vote_success(client, match_with_vote, db):
    from app import MatchVote as MV
    m, team_a, team_b, voter = match_with_vote
    new_rank = list(reversed(team_a))  # reverse order
    resp = client.put(
        f"/matches/{m.id}/votes/{voter}",
        json={"voter_player_id": voter, "rank": new_rank},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    db.expire_all()
    vote = db.query(MV).filter(MV.match_id == m.id, MV.voter_player_id == voter).first()
    assert vote.rank_1 == new_rank[0]
    assert vote.voted_by_admin == True

def test_modify_vote_recomputes_borda(client, match_with_vote, db):
    from app import Match as MatchModel
    m, team_a, team_b, voter = match_with_vote
    new_rank = list(reversed(team_a))
    client.put(
        f"/matches/{m.id}/votes/{voter}",
        json={"voter_player_id": voter, "rank": new_rank},
        headers=ADMIN_HEADERS,
    )
    db.expire_all()
    updated = db.query(MatchModel).get(m.id)
    assert updated.rank_winners is not None

def test_modify_vote_requires_admin(client, match_with_vote):
    m, team_a, team_b, voter = match_with_vote
    resp = client.put(
        f"/matches/{m.id}/votes/{voter}",
        json={"voter_player_id": voter, "rank": team_a},
    )
    assert resp.status_code == 401

def test_modify_vote_not_found_if_no_existing_vote(client, match_with_vote):
    m, team_a, team_b, voter = match_with_vote
    non_voter = team_b[1]  # this player never voted
    resp = client.put(
        f"/matches/{m.id}/votes/{non_voter}",
        json={"voter_player_id": non_voter, "rank": team_a},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 404

def test_modify_vote_wrong_players_returns_422(client, match_with_vote):
    m, team_a, team_b, voter = match_with_vote
    resp = client.put(
        f"/matches/{m.id}/votes/{voter}",
        json={"voter_player_id": voter, "rank": team_b},  # wrong team
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 422

def test_modify_vote_only_last_match(client, match_with_vote, db):
    from app import Match as MatchModel, Player
    m, team_a, team_b, voter = match_with_vote
    # Create a newer match so m is no longer the last
    players = db.query(Player).order_by(Player.id).all()
    new_team_a = [p.id for p in players[:5]]
    new_team_b = [p.id for p in players[5:]]
    newer = MatchModel(
        team_a=",".join(str(i) for i in new_team_a),
        team_b=",".join(str(i) for i in new_team_b),
        is_recorded=True, winner_team="A", goal_diff=1,
    )
    db.add(newer); db.commit()
    resp = client.put(
        f"/matches/{m.id}/votes/{voter}",
        json={"voter_player_id": voter, "rank": team_a},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 400
```

- [ ] **Step 1.3: Run tests — verify they fail**

```bash
cd C:\F5_App\futbol5\futbol5\backend
pytest tests/test_modify_votes.py -v
```

Expected: all tests FAIL with `404 Not Found` (endpoint doesn't exist yet).

- [ ] **Step 1.4: Add the endpoint to `futbol5/backend/app.py`**

Insert after the closing `return {"ok": True}` of `submit_vote` (~line 1111), before `@app.get("/matches/{mid}/votes"`:

```python
@app.put("/matches/{mid}/votes/{voter_player_id}", status_code=200)
def modify_vote(mid: int, voter_player_id: int, data: MatchVoteIn,
                db=Depends(get_session), _: None = Depends(check_admin)):
    last = db.query(Match).filter(Match.is_recorded.is_(True)).order_by(Match.id.desc()).first()
    if not last or last.id != mid:
        raise HTTPException(400, "Solo se puede modificar el último partido")

    m = db.query(Match).get(mid)
    if not m:
        raise HTTPException(404, "Partido no encontrado")

    vote = db.query(MatchVote).filter(
        MatchVote.match_id == mid,
        MatchVote.voter_player_id == voter_player_id,
    ).first()
    if not vote:
        raise HTTPException(404, "El jugador no tiene voto registrado en este partido")

    team_a = csv_split(m.team_a) or []
    team_b = csv_split(m.team_b) or []
    rival_ids = team_b if voter_player_id in team_a else team_a
    if len(data.rank) != 5 or set(data.rank) != set(rival_ids):
        raise HTTPException(422, f"rank debe contener exactamente los 5 jugadores del equipo rival: {rival_ids}")

    vote.rank_1, vote.rank_2, vote.rank_3, vote.rank_4, vote.rank_5 = data.rank
    vote.voted_by_admin = True
    vote.submitted_at = datetime.utcnow()
    db.commit()

    db.refresh(m)
    _borda_recompute(db, m)
    return {"ok": True}
```

- [ ] **Step 1.5: Run tests — verify they pass**

```bash
cd C:\F5_App\futbol5\futbol5\backend
pytest tests/test_modify_votes.py -v
```

Expected: all 6 tests PASS.

- [ ] **Step 1.6: Commit**

```bash
git add futbol5/backend/app.py futbol5/backend/tests/conftest.py futbol5/backend/tests/test_modify_votes.py
git commit -m "feat: add PUT /matches/{mid}/votes/{voter_player_id} for admin vote modification"
```

---

### Task 2: Frontend — `openModifyVoteModal` function

**Files:**
- Modify: `futbol5/frontend/index.html` — insert new function after `openAdminVoteModal` (~line 1402)

- [ ] **Step 2.1: Add `openModifyVoteModal` after the closing `}` of `openAdminVoteModal` (line 1402)**

Insert this function between `openAdminVoteModal` and `// ---- TEAM GENERATOR ----`:

```js
async function openModifyVoteModal(match, onDone) {
  if (!state.adminPin) { toast('Sesión de admin expirada — ingresá el PIN en la pestaña Admin.', 'err'); return; }

  let voteData;
  try {
    voteData = await api(`/matches/${match.id}/votes`);
  } catch(e) { toast('Error cargando votos', 'err'); return; }

  const voters = voteData.individual_votes || [];
  if (!voters.length) { toast('No hay votos para modificar', 'err'); return; }

  const teamA = match.team_a || [];
  const teamB = match.team_b || [];

  const overlay = h('div', { class:'overlay', onClick: e => { if(e.target===overlay) overlay.remove(); } });
  const modal = h('div', { class:'modal' });
  modal.appendChild(h('div', { class:'modal-title' },
    h('span',{},'✏️ Modificar voto'),
    h('button',{class:'close-btn',onClick:()=>overlay.remove()},'×')
  ));
  modal.appendChild(h('div',{style:'font-size:.78rem;color:var(--yellow);margin-bottom:6px'},'⚡ Modo administrador'));
  modal.appendChild(h('div',{style:'font-size:.75rem;background:rgba(234,179,8,.1);border:1px solid rgba(234,179,8,.3);border-radius:6px;padding:8px 10px;margin-bottom:12px;color:#fbbf24'},
    '⚠️ Esta acción reemplaza el voto del jugador y recalcula el ranking'
  ));

  modal.appendChild(h('div',{style:'font-size:.75rem;color:var(--muted2);margin-bottom:4px;font-weight:700;text-transform:uppercase'},'Modificar voto de:'));
  const sel = h('select',{style:'width:100%;background:#1e293b;border:1px solid rgba(255,255,255,.2);border-radius:8px;padding:8px;color:#f1f5f9;font-size:.9rem;margin-bottom:14px'});
  voters.forEach(v => {
    sel.appendChild(h('option',{value:v.voter_player_id,style:'background:#1e293b;color:#f1f5f9'},v.voter_name));
  });
  modal.appendChild(sel);

  const listWrap = h('div',{});
  modal.appendChild(listWrap);

  function renderForVoter(voterId) {
    listWrap.innerHTML = '';
    const vid = Number(voterId);
    const voterInA = teamA.includes(vid);
    const rivalIds = voterInA ? teamB : teamA;
    const existingVote = voters.find(v => v.voter_player_id === vid);
    let ranked = existingVote ? [...existingVote.rank] : [...rivalIds];
    let dragSrcIdx = null;
    const dropZone = h('div',{class:'rank-drop-zone',style:'margin-bottom:14px'});

    function renderList() {
      dropZone.innerHTML = '';
      ranked.forEach((pid, idx) => {
        const p = state.players.find(x => x.id === pid);
        const item = h('div',{class:'rank-item',draggable:'true'});
        const colors = ['#fbbf24','#e2e8f0','#cd7f32','var(--muted2)','var(--muted2)'];
        item.appendChild(h('span',{style:`font-family:"Barlow Condensed",sans-serif;font-size:1.1rem;font-weight:900;color:${colors[idx]||'var(--muted)'};min-width:26px`},`${idx+1}°`));
        item.appendChild(h('span',{style:'flex:1;font-size:.9rem'},p ? playerLabel(p) : `#${pid}`));
        item.appendChild(h('span',{style:'color:var(--muted);font-size:1rem;cursor:grab;padding:0 4px'},'⠿'));
        item.ondragstart = () => { dragSrcIdx = idx; setTimeout(()=>item.classList.add('dragging'),0); };
        item.ondragend = () => { item.classList.remove('dragging'); dragSrcIdx = null; };
        item.ondragover = e => e.preventDefault();
        item.ondrop = e => {
          e.preventDefault();
          if (dragSrcIdx === null || dragSrcIdx === idx) return;
          const moved = ranked.splice(dragSrcIdx,1)[0]; ranked.splice(idx,0,moved); renderList();
        };
        dropZone.appendChild(item);
      });
      dropZone.ondragover = e => { e.preventDefault(); dropZone.classList.add('drag-over'); };
      dropZone.ondragleave = () => dropZone.classList.remove('drag-over');
      dropZone.ondrop = e => { e.preventDefault(); dropZone.classList.remove('drag-over'); };
    }
    renderList();
    listWrap.appendChild(dropZone);

    const voterName = voters.find(v => v.voter_player_id === vid)?.voter_name || `#${vid}`;
    const confirmBtn = h('button',{class:'btn btn-green',style:'width:100%',onClick:async()=>{
      try {
        await api(`/matches/${match.id}/votes/${vid}`,{method:'PUT',body:JSON.stringify({
          voter_player_id: vid, rank: ranked
        })});
        toast(`Voto de ${voterName} actualizado ✓`);
        overlay.remove();
        onDone?.();
      } catch(e) {
        toast('Error: '+e.message,'err');
      }
    }},`Guardar voto de ${voterName} →`);
    listWrap.appendChild(confirmBtn);
  }

  sel.addEventListener('change', () => renderForVoter(sel.value));
  renderForVoter(voters[0].voter_player_id);

  overlay.appendChild(modal);
  document.body.appendChild(overlay);
}
```

- [ ] **Step 2.2: Commit**

```bash
git add futbol5/frontend/index.html
git commit -m "feat: add openModifyVoteModal function"
```

---

### Task 3: Frontend — Button in match card

**Files:**
- Modify: `futbol5/frontend/index.html` (~line 1734, inside the `load` function)

- [ ] **Step 3.1: Add lastMatchId variable before `matches.forEach`**

Find this line (~line 1740):
```js
      matches.forEach(m => {
```

Insert BEFORE it:
```js
      const lastMatchId = matches.filter(m => m.is_recorded).reduce((max, m) => Math.max(max, m.id), 0);
```

- [ ] **Step 3.2: Add "Modificar voto" button inside the admin actions block**

Find this block (~line 1864):
```js
          } else if (m.winner_team !== 'D' && m.voting_deadline) {
            if (new Date(m.voting_deadline) > new Date()) {
              actions.appendChild(h('button',{class:'btn btn-sm',style:'background:rgba(234,179,8,.15);color:var(--yellow);border:1px solid rgba(234,179,8,.3)',
                onClick:()=>openAdminVoteModal(m, ()=>{ load(); updateHistorialBadge(); })},'⚽ Votar por otro'));
            }
            actions.appendChild(h('button',{class:'btn btn-sm',style:'background:rgba(99,102,241,.15);color:#a5b4fc;border:1px solid rgba(99,102,241,.3)',
              onClick:()=>openVotesAuditModal(m)},'⚡ Ver votos'));
          }
```

Replace with:
```js
          } else if (m.winner_team !== 'D' && m.voting_deadline) {
            if (new Date(m.voting_deadline) > new Date()) {
              actions.appendChild(h('button',{class:'btn btn-sm',style:'background:rgba(234,179,8,.15);color:var(--yellow);border:1px solid rgba(234,179,8,.3)',
                onClick:()=>openAdminVoteModal(m, ()=>{ load(); updateHistorialBadge(); })},'⚽ Votar por otro'));
            }
            if (m.id === lastMatchId) {
              actions.appendChild(h('button',{class:'btn btn-sm',style:'background:rgba(168,85,247,.15);color:#d8b4fe;border:1px solid rgba(168,85,247,.3)',
                onClick:()=>openModifyVoteModal(m, ()=>{ load(); })},'✏️ Modificar voto'));
            }
            actions.appendChild(h('button',{class:'btn btn-sm',style:'background:rgba(99,102,241,.15);color:#a5b4fc;border:1px solid rgba(99,102,241,.3)',
              onClick:()=>openVotesAuditModal(m)},'⚡ Ver votos'));
          }
```

- [ ] **Step 3.3: Commit**

```bash
git add futbol5/frontend/index.html
git commit -m "feat: show Modificar voto button on last match card"
```

---

### Task 4: Push to ACP and verify

- [ ] **Step 4.1: Push to ACP**

```bash
git push origin ACP
```

- [ ] **Step 4.2: Check GitHub Actions**

Go to `https://github.com/JuanI-R/F5_APP/actions` — wait for the deploy to go green.

- [ ] **Step 4.3: Test in browser**

1. Open `http://172.29.76.225`
2. Log in as admin (PIN: 1234)
3. Set backend URL to `http://172.29.76.225:8000` in settings if not set
4. Go to **Historial** tab → **Jugados**
5. Verify the last match shows "✏️ Modificar voto" button
6. Click it → modal opens with voters dropdown and pre-populated rankings
7. Reorder and click Guardar → toast confirms → ranking updates in the card

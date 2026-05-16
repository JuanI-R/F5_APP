from app import ADMIN_PIN

ADMIN_HEADERS = {"X-Admin-Pin": ADMIN_PIN}

def test_modify_vote_success(client, match_with_vote, db):
    from app import MatchVote as MV
    m, team_a, team_b, voter = match_with_vote
    new_rank = list(reversed(team_a))
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
    non_voter = team_b[1]
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
        json={"voter_player_id": voter, "rank": team_b},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 422

def test_modify_vote_only_last_match(client, match_with_vote, db):
    from app import Match as MatchModel, Player
    m, team_a, team_b, voter = match_with_vote
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
